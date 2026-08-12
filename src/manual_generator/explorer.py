from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator as PlaywrightLocator
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from .events import broker
from .models import Approval, Feature, Screenshot, Step, Task, now
from .schemas import BrowserAction, Locator, browser_action_adapter
from .security import risky_action


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
        in {"click", "fill", "select_option", "press", "scroll"}
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
    ):
        self.session = session
        self.client = client
        self.credentials = credentials or {}

    async def explore_task(self, task: Task, task_dir: Path) -> None:
        async with async_playwright() as playwright:
            launch_options = {"headless": task.browser_mode == "headless"}
            try:
                browser = await playwright.chromium.launch(**launch_options)
            except PlaywrightError as exc:
                executable = _fallback_chromium()
                if not executable:
                    raise RuntimeError(
                        "未安装项目 Chromium，且未找到可用的本机 Chrome"
                    ) from exc
                browser = await playwright.chromium.launch(
                    **launch_options,
                    executable_path=str(executable),
                )
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await context.new_page()
            try:
                await page.goto(task.start_url or "about:blank", wait_until="domcontentloaded")
                for feature in [
                    item
                    for item in task.features
                    if item.selected and item.status != "completed"
                ]:
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
                await context.close()
                await browser.close()

    async def _explore_feature(self, page: Page, task: Task, feature: Feature, task_dir: Path) -> None:
        feature.status = "processing"
        feature.error = None
        feature.steps.clear()
        await self.session.commit()
        entry_url = urljoin(task.start_url or "", feature.entry_path)
        if not same_origin(task.start_url or "", entry_url):
            feature.status, feature.error = "failed", "功能入口跨域"
            await self.session.commit()
            return
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
                goal=feature.goal or feature.title,
                url=page.url,
                page_text=text,
                history=history,
                screenshot=preview,
            )
            if early_finish_without_interaction(action, history, text, feature.title):
                raise RuntimeError("页面包含可交互功能，但模型未执行有效交互，不能直接标记完成")
            action_data = action.model_dump(mode="json")
            risk = risky_action(action_data)
            if action.action == "navigate" and not same_origin(task.start_url or "", action.url):
                risk = "动作将导航到起始站点之外"
            if risk:
                approval = Approval(
                    task_id=task.id,
                    action=action_data,
                    reason=risk,
                    status="approved",
                    resolved_at=now(),
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
                        "status": "approved",
                    },
                )
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
                self.session.add(Screenshot(step_id=step.id, path=str(screenshot_path)))
            await self.session.commit()
            history.append({"action": action_data, "result": result, "url": page.url})
            await broker.publish(task.id, "step", {"feature_id": feature.id, "position": position, "action": action.action, "instruction": step.instruction})
            if action.action == "finish":
                feature.status = "completed"
                feature.error = None
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
        }:
            screenshot_path = task_dir / "screenshots" / f"{feature_id}-{position:03d}.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=screenshot_path)
        return result, screenshot_path

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
            return page.locator(target.selector).first
        raise ValueError("动作缺少可用定位条件")


def _fallback_chromium() -> Path | None:
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    candidates = [
        Path(configured) if configured else None,
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    return next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
