from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import sys
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator as PlaywrightLocator
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from .events import broker
from .models import Approval, Artifact, Feature, Screenshot, Step, Task
from .runtime_schemas import ExecutionConfig, FeatureExecution, ordered_ids
from .schemas import BrowserAction, Locator, browser_action_adapter
from .security import risky_action


def effective_browser_mode(mode: str) -> str:
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "headless"
    return mode


async def launch_browser(chromium, mode: str):
    options = {"headless": effective_browser_mode(mode) == "headless"}
    try:
        return await chromium.launch(**options)
    except PlaywrightError as exc:
        if "Executable doesn't exist" not in str(exc):
            raise RuntimeError(f"Chromium启动失败：{exc}") from exc
        executable = _fallback_chromium()
        if not executable:
            raise RuntimeError("未安装项目 Chromium，且未找到可用的本机 Chrome；请执行 python -m playwright install chromium") from exc
        return await chromium.launch(**options, executable_path=str(executable))


def same_origin(base: str, target: str) -> bool:
    base_url, target_url = urlparse(base), urlparse(urljoin(base, target))
    def port(value):
        return value.port or (443 if value.scheme == "https" else 80)

    return (base_url.scheme, base_url.hostname, port(base_url)) == (
        target_url.scheme,
        target_url.hostname,
        port(target_url),
    )


def passive_stale_action(action: BrowserAction, current_url: str) -> bool:
    if action.action in {"screenshot", "wait_for"}:
        return True
    return action.action == "navigate" and urljoin(current_url, action.url) == current_url


def has_interactive_controls(page_text: str) -> bool:
    markers = ("选择文件", "上传", "下载", "计算", "提交", "保存", "新建", "删除", "编辑")
    return any(marker in page_text for marker in markers)


def meaningful_interaction(history: list[dict]) -> bool:
    return any(
        item.get("action", {}).get("action")
        in {"click", "fill", "select_option", "press", "scroll", "upload", "download"}
        for item in history
    )


COMPLETION_MARKERS = (
    "已自动加载",
    "已加载",
    "已上传",
    "上传成功",
    "已导入",
    "导入成功",
    "已选择",
    "处理完成",
    "操作成功",
)
FEATURE_STOP_WORDS = (
    "上传",
    "下载",
    "导入",
    "导出",
    "选择",
    "执行",
    "文件",
    "明文",
    "加密",
    "结果",
    "excel",
    "xlsx",
    "xls",
    "csv",
    "enc",
)


def page_shows_completed_feature(feature_title: str, page_text: str) -> bool:
    normalized_text = "".join(page_text.lower().split())
    if not any(marker in normalized_text for marker in COMPLETION_MARKERS):
        return False
    subject = "".join(feature_title.lower().split())
    for word in FEATURE_STOP_WORDS:
        subject = subject.replace(word, "")
    terms = [item for item in subject.replace("/", " ").replace("-", " ").split() if len(item) >= 2]
    if subject and not terms:
        terms = [subject]
    return any(term in normalized_text for term in terms)


def early_finish_without_interaction(
    action: BrowserAction,
    history: list[dict],
    page_text: str,
    feature_title: str = "",
) -> bool:
    return (
        action.action == "finish"
        and not meaningful_interaction(history)
        and has_interactive_controls(page_text)
        and not page_shows_completed_feature(feature_title, page_text)
    )


def action_instruction(action: BrowserAction) -> str:
    if action.action == "finish":
        return action.summary.strip() or "确认功能操作已完成"
    return action.instruction.strip() or action.reason.strip() or action.action


class Explorer:
    def __init__(
        self,
        session: AsyncSession,
        client,
        credentials: dict[str, str] | None = None,
        *, execution=None, runtime_plan=None, record=None,
    ):
        self.session = session
        self.client = client
        self.credentials = credentials or {}
        self.execution = execution or ExecutionConfig()
        self.runtime_plan = runtime_plan
        self.record = record
        self.file_records = {}
        self.rule = FeatureExecution()
        self.responses = []
        self.dialogs = []
        self.downloaded = False
        self.uploaded = set()
        self.pending_dialogs = set()

    async def explore_task(self, task: Task, task_dir: Path) -> None:
        async with async_playwright() as playwright:
            browser = await launch_browser(playwright.chromium, task.browser_mode)
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            if self.runtime_plan:
                async def scoped_request(route):
                    if same_origin(task.start_url or "", route.request.url):
                        await route.continue_()
                    else:
                        await route.abort("blockedbyclient")
                await context.route("**/*", scoped_request)
            page = await context.new_page()
            self.task = task
            from .runtime_api import files_for
            self.file_records = {f.id: f for f in await files_for(self.session, task.id)} if self.runtime_plan else {}
            def handle_dialog(dialog):
                handler = asyncio.create_task(self._dialog(dialog))
                self.pending_dialogs.add(handler)
            page.on("dialog", handle_dialog)
            page.on("response", self._response)
            try:
                await page.goto(task.start_url or "about:blank", wait_until="domcontentloaded")
                if self.runtime_plan:
                    from .browser_checks import verify_page
                    await verify_page(page, self.runtime_plan, task_dir / "diagnostics" / f"{self.record.run_id}-readiness.png")
                features = {f.id: f for f in task.features if f.selected}
                dependencies = {key: self.execution.features.get(key, FeatureExecution()).depends_on for key in features}
                for identity in ordered_ids(dependencies):
                    feature = features[identity]
                    if any(features[key].status != "completed" for key in dependencies[identity]):
                        feature.status, feature.error = "blocked", "前置功能未通过验证"
                        if self.record:
                            self.record.data = {**self.record.data, "features": {**self.record.data.get("features", {}), identity: {"status": "blocked", "error": feature.error}}}
                        await self.session.commit()
                        continue
                    try:
                        await self._explore_feature(page, task, feature, task_dir)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        feature.status = "failed"
                        feature.error = str(exc)
                        await self.session.commit()
                        await broker.publish(
                            task.id,
                            "feature",
                            {
                                "feature_id": feature.id,
                                "status": "failed",
                                "error": feature.error,
                            },
                        )
                    finally:
                        if self.record:
                            self.record.data = {**self.record.data, "features": {**self.record.data.get("features", {}), feature.id: {"status": feature.status, "error": feature.error, "responses": self.responses, "dialogs": self.dialogs, "uploaded_file_ids": sorted(self.uploaded), "downloaded": self.downloaded}}}
                            await self.session.commit()
            finally:
                for handler in self.pending_dialogs:
                    if not handler.done():
                        handler.cancel()
                await asyncio.gather(*self.pending_dialogs, return_exceptions=True)
                await context.close()
                await browser.close()

    async def _explore_feature(self, page: Page, task: Task, feature: Feature, task_dir: Path) -> None:
        feature.status = "processing"
        feature.error = None
        self.rule = self.execution.features.get(feature.id, FeatureExecution())
        self.responses, self.dialogs, self.uploaded, self.downloaded = [], [], set(), False
        await self.session.commit()
        entry_url = urljoin(task.start_url or "", feature.entry_path)
        if not same_origin(task.start_url or "", entry_url):
            feature.status, feature.error = "failed", "功能入口跨域"
            await self.session.commit()
            return
        if page.url != entry_url:
            await page.goto(entry_url, wait_until="domcontentloaded")
        history: list[dict] = []
        stale_count = 0
        started = asyncio.get_running_loop().time()
        preview = task_dir / "screenshots" / f"preview-{feature.id}.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        previous = ""
        start_position = max((step.position for step in feature.steps), default=0) + 1
        for position in range(start_position, start_position + 60):
            if asyncio.get_running_loop().time() - started > 900:
                raise TimeoutError("单个功能探索超过 15 分钟")
            text = await page.locator("body").inner_text(timeout=10000)
            await page.screenshot(path=preview, type="jpeg", quality=72)
            fingerprint = hashlib.sha256(f"{page.url}\n{text[:12000]}".encode()).hexdigest()
            stale_count = stale_count + 1 if fingerprint == previous else 0
            previous = fingerprint
            action = await self.client.next_action(
                goal=(feature.goal or feature.title) + "\n已确认测试文件：" + json.dumps([{"id": f.id, "name": f.name, "purpose": f.purpose, "size": f.size, "media_type": f.media_type} for f in self.file_records.values() if f.id in self.rule.file_ids], ensure_ascii=False) + "\n验证条件：" + self.rule.model_dump_json() + "\n已验证接口结果：" + json.dumps(self.responses, ensure_ascii=False) + "\n对话框提示：" + json.dumps(self.dialogs, ensure_ascii=False),
                url=page.url,
                page_text=text + "\n文件控件：" + json.dumps(await page.locator('input[type="file"]').evaluate_all('(els)=>els.map((e,index)=>({selector:"input[type=file]",index,accept:e.accept,disabled:e.disabled}))'), ensure_ascii=False),
                history=history + [{"verified_state": {"uploaded_file_ids": sorted(self.uploaded), "download_complete": self.downloaded}, "instruction": "已验证下载完成时必须 finish，不得再次下载"}],
                screenshot=preview,
            )
            if early_finish_without_interaction(action, history, text, feature.title):
                raise RuntimeError("页面包含可交互功能，但模型未执行有效交互，不能直接标记完成")
            action_data = action.model_dump(mode="json")
            if action.action == "request_approval":
                proposed = browser_action_adapter.validate_python(action.proposed_action)
                if proposed.action in {"upload", "download"}:
                    action = proposed
                    action_data = action.model_dump(mode="json")
            risk = risky_action(action_data)
            if action.action in {"upload", "download"} and same_origin(task.start_url or "", page.url):
                risk = None
            if action.action == "navigate" and not same_origin(task.start_url or "", action.url):
                risk = "动作将导航到起始站点之外"
            if risk:
                approval = Approval(
                    task_id=task.id,
                    action=action_data,
                    reason=risk,
                    status="pending",
                )
                self.session.add(approval)
                await self.session.commit()
                await broker.publish(
                    task.id,
                    "approval",
                    {
                        "id": approval.id,
                        "reason": risk,
                        "action": action_data,
                        "status": "pending",
                    },
                )
                await self._await_approval(approval)
                if action.action == "request_approval":
                    action = browser_action_adapter.validate_python(action.proposed_action)
                    if action.action == "request_approval":
                        raise ValueError("审批动作不能嵌套 request_approval")
                    action_data = action.model_dump(mode="json")
            if (
                action.action != "finish"
                and stale_count >= 3
                and passive_stale_action(action, page.url)
            ):
                raise RuntimeError("页面连续三次没有变化，且模型未选择新的交互动作")
            result, screenshot_path = await self._execute(page, action, task_dir, feature.id, position)
            step = Step(
                feature_id=feature.id,
                position=position,
                action=action.action,
                target=json.dumps(action_data.get("target"), ensure_ascii=False) if action_data.get("target") else None,
                instruction=action_instruction(action),
                url=page.url,
                result=result,
                fingerprint=fingerprint,
            )
            self.session.add(step)
            await self.session.flush()
            if screenshot_path:
                self.session.add(Screenshot(step_id=step.id, path=str(screenshot_path), included=False))
            await self.session.commit()
            history.append({"action": action_data, "result": result, "url": page.url})
            await broker.publish(task.id, "step", {"feature_id": feature.id, "position": position, "action": action.action, "instruction": step.instruction})
            if action.action == "finish":
                if set(self.rule.file_ids) != self.uploaded:
                    raise ValueError("尚未完成已确认文件的上传与结果验证")
                if self.rule.download_extension and not self.downloaded:
                    raise ValueError("尚未捕获并验证实际下载文件")
                if self.rule.success_response and not self.responses:
                    raise ValueError("未验证到预期接口成功结果")
                if self.rule.success_text and self.rule.success_text not in await page.locator("body").inner_text() and not any(self.rule.success_text in d for d in self.dialogs):
                    raise ValueError("未验证到预期成功提示")
                feature.status = "completed"
                feature.error = None
                from sqlalchemy import select
                screenshots = (await self.session.scalars(select(Screenshot).join(Step).where(Step.feature_id == feature.id, Step.position >= start_position))).all()
                for screenshot in screenshots:
                    screenshot.included = True
                await self.session.commit()
                return
        raise RuntimeError("功能探索达到 60 步上限")

    async def _execute(self, page: Page, action: BrowserAction, task_dir: Path, feature_id: str, position: int) -> tuple[str, Path | None]:
        screenshot_path: Path | None = None
        result = "success"
        if action.action == "navigate":
            await page.goto(action.url, wait_until="domcontentloaded")
        elif action.action == "click":
            await self._locator(page, action.target).click()
        elif action.action == "fill":
            value = self.credentials.get(action.credential_ref or "", action.value or "")
            await self._locator(page, action.target).fill(value)
        elif action.action == "upload":
            if action.file_id in self.uploaded:
                return "该文件已经上传并验证成功，不得重复上传；请完成当前功能", None
            if action.file_id not in self.rule.file_ids or action.file_id not in self.file_records:
                raise ValueError("模型引用了未确认或跨任务测试文件")
            if not same_origin(self.task.start_url, page.url):
                raise ValueError("禁止向未确认站点上传文件")
            file = self.file_records[action.file_id]
            content = Path(file.path).read_bytes()
            if hashlib.sha256(content).hexdigest() != file.sha256:
                raise ValueError("测试文件已变化，请重新确认")
            self.responses = []
            self.dialogs = []
            await self._locator(page, action.target).set_input_files({"name": file.name, "mimeType": file.media_type, "buffer": content})
            for _ in range(150):
                text_success = self.rule.success_text and (self.rule.success_text in await page.locator("body").inner_text() or any(self.rule.success_text in d for d in self.dialogs))
                if self.responses or text_success:
                    self.uploaded.add(file.id)
                    break
                await asyncio.sleep(.1)
            else:
                raise ValueError("文件已选择，但未验证到上传成功结果")
        elif action.action == "download":
            if self.downloaded:
                return "下载已完成，非空文件与类型已验证；请使用 finish 结束当前功能", None
            if not self.rule.download_extension or not same_origin(self.task.start_url, page.url):
                raise ValueError("下载用途或目标站点未确认")
            async with page.expect_download(timeout=15000) as pending:
                await self._locator(page, action.target).click()
            download = await pending.value
            if not same_origin(self.task.start_url, download.url) and not download.url.startswith("blob:" + self.task.start_url.rstrip("/")):
                raise ValueError("下载事件来自未确认站点")
            name = Path(download.suggested_filename).name
            if Path(name).suffix.lower() != self.rule.download_extension.lower():
                raise ValueError("下载文件类型与确认用途不一致")
            destination = task_dir / "downloads" / f"{uuid.uuid4()}-{name}"
            destination.parent.mkdir(exist_ok=True)
            await download.save_as(destination)
            if await download.failure() or destination.stat().st_size == 0:
                raise ValueError("下载失败或文件为空")
            if destination.suffix.lower() == ".xlsx":
                with zipfile.ZipFile(destination) as archive:
                    if "xl/workbook.xml" not in archive.namelist():
                        raise ValueError("下载内容不是有效的Excel工作簿")
            self.session.add(Artifact(task_id=self.task.id, kind=destination.suffix.lstrip("."), path=str(destination), mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream", size=destination.stat().st_size))
            self.downloaded = True
            result = json.dumps({"download_complete": True, "name": name, "size": destination.stat().st_size, "type_verified": True, "next_action": "finish"}, ensure_ascii=False)
        elif action.action == "select_option":
            await self._locator(page, action.target).select_option(action.value)
        elif action.action == "press":
            await page.keyboard.press(action.key)
        elif action.action == "scroll":
            amount = action.amount if action.direction == "down" else -action.amount
            await page.mouse.wheel(0, amount)
        elif action.action == "wait_for":
            try:
                if action.text:
                    await page.get_by_text(action.text, exact=False).first.wait_for(
                        timeout=action.timeout_ms
                    )
                else:
                    await page.wait_for_timeout(action.timeout_ms)
            except PlaywrightTimeoutError:
                result = f"等待超时，未出现文本：{action.text}"
        if action.action in {
            "navigate",
            "click",
            "fill",
            "select_option",
            "screenshot",
            "finish",
            "upload",
            "download",
        }:
            screenshot_path = task_dir / "screenshots" / f"{feature_id}-{position:03d}.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=screenshot_path)
        return result, screenshot_path

    async def _response(self, response):
        rule = self.rule
        expected = rule.success_response
        if not expected or not same_origin(self.task.start_url, response.url):
            return
        if urlparse(response.url).path != expected.path or response.request.method != expected.method.upper():
            return
        try:
            data = await response.json()
            if self.rule is rule and 200 <= response.status < 300 and isinstance(data, dict) and all(data.get(key) == value for key, value in expected.json_equals.items()):
                observed = {key: data[key] for key in expected.capture_fields if key in data and isinstance(data[key], (str, int, bool, float)) and not any(word in key.lower() for word in ("key", "token", "password", "secret"))}
                self.responses.append({"path": expected.path, "method": expected.method, "status": response.status, "verified": True, "observed": observed})
        except Exception:
            return

    async def _dialog(self, dialog):
        self.dialogs.append(dialog.message[:1000])
        if dialog.type == "alert":
            await dialog.accept()
            return
        from .database import SessionLocal
        async with SessionLocal() as session:
            approval = Approval(task_id=self.task.id, action={"action": "confirm_dialog", "message": dialog.message[:1000]}, reason="页面请求确认", status="pending")
            session.add(approval)
            await session.commit()
            await broker.publish(self.task.id, "approval", {"id": approval.id, "status": "pending", "reason": approval.reason})
            try:
                await self._await_approval(approval, session)
                await dialog.accept()
            except (ValueError, TimeoutError):
                await dialog.dismiss()

    async def _await_approval(self, approval, session=None):
        session = session or self.session
        for _ in range(900):
            await session.refresh(approval)
            if approval.status == "approved":
                return
            if approval.status == "rejected":
                raise ValueError("用户拒绝了风险操作")
            await asyncio.sleep(1)
        raise TimeoutError("等待人工审批超时")

    @staticmethod
    def _locator(page: Page, target: Locator) -> PlaywrightLocator:
        if target.test_id:
            return page.get_by_test_id(target.test_id).first
        if target.label:
            return page.get_by_label(target.label, exact=False).first
        if target.placeholder:
            return page.get_by_placeholder(target.placeholder, exact=False).first
        if target.role:
            return page.get_by_role(target.role, name=target.name, exact=False).first
        if target.text:
            return page.get_by_text(target.text, exact=False).first
        if target.selector:
            return page.locator(target.selector).nth(target.index)
        raise ValueError("动作缺少可用定位条件")


def _fallback_chromium() -> Path | None:
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    candidates = [
        Path(configured) if configured else None,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    return next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
