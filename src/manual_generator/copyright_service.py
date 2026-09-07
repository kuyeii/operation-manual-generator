from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import mimetypes
from pathlib import Path

from pydantic import Field
from sqlalchemy import update

from . import database
from .config import get_settings
from .copyright_documents import build_package
from .copyright_schemas import (
    STAGES,
    Business,
    BusinessFeature,
    Drafts,
    Registration,
    Section,
    StrictModel,
)
from .copyright_sources import digest, redact, scan_sources, verify_sources
from .events import broker
from .llm import ModelOptions, OpenAICompatibleClient
from .models import Artifact, CopyrightBatch, CopyrightCase, Task, new_id

RULES = {
    "baseline": "Fokkyp SoftwareCopyright-Skill v1.3 (2026-07-18)",
    "design_reference": "IvanCodesDev software-certificate-skill: evidence and quality gates; independently implemented design specification",
    "checked_at": "2026-09-07",
    "deposit": "一般交存：不超过60页提交全部，超过60页取连续前后各30页；代码每页50行，末页可不足",
    "notice": "字段字数上限以提交当日登记系统为准；经验性篇幅不作为法定要求。合作、委托、修改、继受等权属证明需另行核验。",
}
JOBS: dict[str, asyncio.Task] = {}
LOCKS: dict[str, asyncio.Lock] = {}
DESIGN_TITLES = ["软件概述与设计目标", "总体架构", "模块详细设计", "数据结构与存储设计", "接口设计", "关键处理流程", "安全与异常处理", "部署与运行设计"]


class ReviewFinding(StrictModel):
    blocking: bool
    chapter_title: str
    document_quote: str
    evidence_id: str
    source_quote: str
    message: str


class ReviewResult(StrictModel):
    issues: list[ReviewFinding] = Field(max_length=30)


class ModuleAnalysis(Business):
    technical_notes: list[BusinessFeature] = Field(min_length=1, max_length=100)


def lock_for(task_id: str) -> asyncio.Lock:
    return LOCKS.setdefault(task_id, asyncio.Lock())


def model_client() -> OpenAICompatibleClient:
    settings = get_settings()
    return OpenAICompatibleClient(ModelOptions(api_key=settings.llm_api_key or "", base_url=settings.llm_base_url, model=settings.llm_model, protocol=settings.llm_protocol))


def task_dir(task_id: str) -> Path:
    return get_settings().data_dir.resolve() / "tasks" / task_id


def invalidate(case: CopyrightCase, stage: str) -> None:
    index = STAGES.index(stage)
    case.confirmations = {key: value for key, value in case.confirmations.items() if key in STAGES[:index]}
    data = copy.deepcopy(case.data)
    if stage != "drafts":
        data.pop("drafts", None)
        data.pop("draft_checkpoint", None)
        data.pop("draft_runtime", None)
    data.pop("reviewed_digest", None)
    data.pop("review_issues", None)
    data.pop("review_resolutions", None)
    case.data = data
    case.revision += 1
    if case.status != "running":
        case.status = "idle"
    case.error = None


def require_confirmations(case: CopyrightCase, through: str) -> None:
    for stage in STAGES[:STAGES.index(through) + 1]:
        if stage not in case.confirmations or case.confirmations[stage].get("digest") != digest(case.data.get(stage)):
            raise ValueError(f"请先确认{ {'business': '业务理解', 'registration': '登记信息', 'sources': '源码选择', 'drafts': '全部草稿'}[stage]}")


def runtime_evidence(task: Task) -> tuple[list[dict], dict, str]:
    features, screenshots = [], {}
    for feature in task.features:
        if not feature.selected:
            continue
        steps = []
        for step in feature.steps:
            item = {"id": step.id, "instruction": redact(step.instruction), "result": step.result}
            screenshot = step.screenshot
            if screenshot and screenshot.included:
                path = Path(screenshot.path)
                if path.is_file() and path.resolve().is_relative_to(task_dir(task.id)):
                    screenshots[screenshot.id] = {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "instruction": redact(step.instruction), "feature_id": feature.id}
                    item["screenshot_id"] = screenshot.id
            steps.append(item)
        features.append({"id": feature.id, "title": feature.title, "goal": redact(feature.goal), "status": feature.status, "steps": steps})
    return features, screenshots, digest({"features": features, "screenshots": screenshots})


def validate_runtime(task: Task) -> tuple[list[dict], dict, str]:
    features, screenshots, fingerprint = runtime_evidence(task)
    if not features:
        raise ValueError("请先完成并审核至少一项功能探索")
    for feature in features:
        if feature["status"] != "completed" or not feature["steps"]:
            raise ValueError(f"功能“{feature['title']}”尚未完成，不能生成正式软著草稿")
        if not any(image["feature_id"] == feature["id"] for image in screenshots.values()):
            raise ValueError(f"功能“{feature['title']}”缺少已纳入的真实截图，请完成截图审核")
    return features, screenshots, fingerprint


def evidence_ids(data: dict) -> set[str]:
    return {item["id"] for item in data["inventory"]["files"] + data["inventory"]["documents"]}


def validate_references(value: Business | Drafts, data: dict) -> None:
    allowed = evidence_ids(data)
    entries = value.features + value.technical_notes if isinstance(value, Business) else value.manual + value.design
    for entry in entries:
        if not set(entry.evidence_ids).issubset(allowed):
            raise ValueError(f"“{entry.title}”引用了不存在的项目证据")


def draft_blockers(task: Task, case: CopyrightCase) -> list[str]:
    if not case.data.get("drafts"):
        return ["尚未生成完整草稿"]
    data = case.data
    drafts = Drafts.model_validate(data["drafts"])
    validate_references(drafts, data)
    features, screenshots, fingerprint = validate_runtime(task)
    errors = []
    if fingerprint != data.get("draft_runtime"):
        errors.append("功能或截图已变化，请重新生成草稿")
    if data.get("reviewed_digest") != digest(data["drafts"]):
        errors.append("请先完成草稿内容复核")
    for issue in data.get("review_issues", []):
        resolution = data.get("review_resolutions", {}).get(digest(issue), {})
        if resolution.get("draft_digest") != digest(data["drafts"]):
            errors.append(issue)
    for feature in features:
        chapters = [section for section in drafts.manual if section.feature_id == feature["id"]]
        if not chapters or not any(section.screenshot_ids for section in chapters):
            errors.append(f"操作手册未覆盖功能“{feature['title']}”及其截图")
    for section in drafts.manual + drafts.design:
        if any(image_id not in screenshots or (section.feature_id and screenshots[image_id]["feature_id"] != section.feature_id) for image_id in section.screenshot_ids):
            errors.append(f"章节“{section.title}”截图失效或不属于该功能")
        if section.feature_id and section.feature_id not in {feature["id"] for feature in features}:
            errors.append(f"章节“{section.title}”关联的功能已失效")
        content = "\n".join(section.paragraphs)
        if any(token in content for token in ("TODO", "待补充", "待填写", "截图预留", "{软件名称}", "[已脱敏]")):
            errors.append(f"章节“{section.title}”含未完成的占位内容")
    return errors


async def checkpoint(session, case: CopyrightCase, data: dict, progress: str) -> None:
    case.data = copy.deepcopy(data)
    case.progress = progress
    await session.commit()
    await broker.publish(case.task_id, "copyright", {"status": case.status, "progress": progress})


async def analyze(session, case: CopyrightCase, task: Task) -> None:
    inventory = await asyncio.to_thread(scan_sources, task_dir(task.id) / "workspace")
    data = copy.deepcopy(case.data)
    previous = data.get("analysis_fingerprint")
    data.update({"inventory": inventory, "rules": RULES, "analysis_fingerprint": inventory["fingerprint"]})
    if previous != inventory["fingerprint"] or data.get("analysis_schema_version") != 2:
        data["analysis_checkpoint"] = []
    data["analysis_schema_version"] = 2
    summaries = data.setdefault("analysis_checkpoint", [])
    chunks, current, size = [], [], 0
    for item in inventory["files"] + inventory["documents"]:
        if size > 22000:
            chunks.append(current)
            current, size = [], 0
        current.append(item)
        size += len(json.dumps(item, ensure_ascii=False))
    if current:
        chunks.append(current)
    client = model_client()
    for index, chunk in enumerate(chunks):
        if index < len(summaries):
            continue
        result = await client.structured_document("根据项目源码证据分析软件业务。证据不足时描述能确认的实际模块，不推测行业或外部系统；环境信息只能来自项目配置。technical_notes必须记录真实技术事实：模块名称、函数和接口签名、数据字段、处理步骤、依赖关系及异常分支；源码未展示的实现应注明证据边界，不推断。每条事实引用输入证据ID。", {"evidence": chunk}, ModuleAnalysis)
        validate_references(result, data)
        summaries.append(result.model_dump())
        await checkpoint(session, case, data, f"已分析源码证据 {index + 1}/{len(chunks)}")
    if len(json.dumps(summaries, ensure_ascii=False)) > 90000:
        raise ValueError("业务证据摘要超过本次分析容量，请缩小申请范围后重试")
    if len(summaries) == 1:
        business = Business.model_validate(summaries[0])
    else:
        business = await client.structured_document("综合模块摘要，合并重复功能，形成同一软件的业务理解。保留原始证据ID。", {"modules": summaries}, Business)
    validate_references(business, data)
    data["business"] = business.model_dump()
    data.setdefault("registration", Registration(software_name=task.name).model_dump())
    data["registration"].update({"languages": ", ".join(inventory["languages"]), "purpose": business.purpose, "industry": business.industry, "main_functions": "；".join(feature.description for feature in business.features), "development_tools": business.development_environment, "supporting_software": business.runtime_environment})
    # Model evidence ranks files; the user sees and confirms the resulting full-file sequence.
    ranked = list(dict.fromkeys(eid for feature in business.features for eid in feature.evidence_ids))
    candidates = [item["path"] for item in inventory["files"]]
    data["sources"] = {"paths": [path for path in ranked if path in candidates] + [path for path in candidates if path not in ranked]}
    invalidate(case, "business")
    for key in ("drafts", "draft_checkpoint", "draft_runtime", "reviewed_digest", "review_issues"):
        data.pop(key, None)
    case.data = data
    await checkpoint(session, case, data, "业务理解已生成，等待确认")


def model_context(data: dict) -> dict:
    profile = data["registration"]
    # Registration identity documents and personal facts are only written locally.
    items = data["inventory"]["files"] + data["inventory"]["documents"]
    evidence = [{"id": item["id"], "excerpt": item["excerpt"]} for item in items]
    # Small projects retain complete evidence. Large projects use verified module summaries.
    if len(json.dumps(evidence, ensure_ascii=False)) > 90000:
        evidence = [{"id": item["id"], "excerpt": item["excerpt"][:500]} for item in items[:100]]
    return {"software": {key: profile[key] for key in ("software_name", "version", "purpose", "main_functions", "technical_features", "runtime_os", "supporting_software")}, "business": data["business"], "modules": data.get("analysis_checkpoint", []), "source_evidence": evidence}


async def generate_drafts(session, case: CopyrightCase, task: Task) -> None:
    require_confirmations(case, "sources")
    data = copy.deepcopy(case.data)
    verify_sources(task_dir(task.id) / "workspace", data["inventory"])
    features, screenshots, fingerprint = validate_runtime(task)
    if data.get("draft_runtime") != fingerprint:
        data["draft_checkpoint"] = {}
    data["draft_runtime"] = fingerprint
    chapters = data.setdefault("draft_checkpoint", {})
    context = model_context(data)
    client = model_client()
    jobs = [("manual-intro", "软件概述与使用环境", "manual", None)]
    jobs += [(f"manual-{feature['id']}", feature["title"], "manual", feature) for feature in features]
    jobs += [(f"design-{index}", title, "design", None) for index, title in enumerate(DESIGN_TITLES)]
    all_sections = {"manual": [], "design": []}
    for index, (key, title, kind, feature) in enumerate(jobs):
        if key not in chapters:
            instruction = (
                "编写中文软件操作手册的一章，以用户视角描述用途、实际输入、操作顺序和结果。严格依据真实步骤，不编造菜单或结果。"
                if kind == "manual" else
                "编写中文技术设计说明书的一章。描述实际架构、模块、数据、接口或处理逻辑，引用真实源码证据；没有实现的机制不要写成已实现，说明证据可确认的边界。"
            )
            section = await client.structured_document(instruction, {**context, "chapter_title": title, "runtime_feature": feature, "source_selection": data["sources"]}, Section)
            section.title = title
            section.feature_id = feature["id"] if feature else None
            section.screenshot_ids = [image_id for image_id, image in screenshots.items() if feature and image["feature_id"] == feature["id"]]
            if not set(section.evidence_ids).issubset(evidence_ids(data)):
                raise ValueError(f"章节“{title}”包含无效证据，重试本阶段可重新生成")
            chapters[key] = section.model_dump()
            await checkpoint(session, case, data, f"已生成章节 {index + 1}/{len(jobs)}：{title}")
        all_sections[kind].append(chapters[key])
    data["drafts"] = Drafts.model_validate(all_sections).model_dump()
    case.data = data
    invalidate(case, "drafts")
    data = copy.deepcopy(case.data)
    await checkpoint(session, case, data, "草稿已生成，正在复核内容")
    await review_drafts(session, case, task)


async def review_drafts(session, case: CopyrightCase, task: Task) -> None:
    require_confirmations(case, "sources")
    data = copy.deepcopy(case.data)
    data.pop("review_resolutions", None)
    if not data.get("drafts"):
        raise ValueError("请先生成完整草稿")
    features, _, fingerprint = validate_runtime(task)
    if fingerprint != data.get("draft_runtime"):
        raise ValueError("功能或截图已变化，请重新生成草稿")
    validate_references(Drafts.model_validate(data["drafts"]), data)
    issues = []
    client = model_client()
    for kind, sections in data["drafts"].items():
        for attempt in range(2):
            await checkpoint(session, case, data, f"正在复核{'操作手册' if kind == 'manual' else '技术设计说明书'}（第{attempt + 1}次）")
            result = await client.structured_document(
                "复核文档中的错误接口路径、虚构业务功能、错误数据结构及相互矛盾的处理流程。每条问题必须给出逐字文档原句document_quote和逐字源码原句source_quote（不含行号前缀），以及章节标题和证据ID。blocking=true仅用于实质错误。日志引用省略URL等尾部细节、概括性表达、源码的等价写法不构成错误，应blocking=false。任何结论为'无直接矛盾'的条目必须blocking=false或不输出。未出现在摘录中的代码不等于未实现，不能凭缺失证据否定功能。无可核实实质问题返回空issues。",
                {**model_context(data), "document_type": kind, "chapters": sections, "runtime": features if kind == "manual" else []}, ReviewResult,
            )
            data.setdefault("review_findings", {})[kind] = result.model_dump()
            candidates = [finding for finding in result.issues if finding.blocking]
            valid = []
            for finding in candidates:
                chapter = next((section for section in sections if section["title"] == finding.chapter_title), None)
                if not chapter or finding.evidence_id not in evidence_ids(data):
                    continue
                source = (task_dir(task.id) / "workspace" / finding.evidence_id).read_text(encoding="utf-8")
                if finding.document_quote.strip() and finding.source_quote.strip() and finding.document_quote in "\n".join(chapter["paragraphs"]) and finding.source_quote in source:
                    valid.append(finding)
            if len(valid) != len(candidates):
                if attempt == 0:
                    continue
                issues.append(f"{'操作手册' if kind == 'manual' else '技术设计说明书'}的模型复核未返回可定位的原文证据，请重新复核")
                break
            if not valid:
                break
            if attempt == 0:
                for finding in valid:
                    index = next(i for i, section in enumerate(sections) if section["title"] == finding.chapter_title)
                    old = sections[index]
                    corrected = await client.structured_document("根据确切的源码与文档矛盾修正本章节，保留其他已验证内容和原始证据ID。", {**model_context(data), "chapter": old, "finding": finding.model_dump()}, Section)
                    corrected.title = old["title"]
                    corrected.feature_id = old.get("feature_id")
                    corrected.screenshot_ids = old.get("screenshot_ids", [])
                    if not set(corrected.evidence_ids).issubset(evidence_ids(data)):
                        raise ValueError("修正章节引用无效证据，请重试复核")
                    sections[index] = corrected.model_dump()
                case.revision += 1
                await checkpoint(session, case, data, "已按源码修正草稿，正在再次复核")
            else:
                issues.extend(f"{finding.chapter_title}：{finding.message}" for finding in valid)
    data["review_issues"] = issues
    data["reviewed_digest"] = digest(data["drafts"])
    await checkpoint(session, case, data, "草稿复核完成" if not issues else "草稿有待修正项")


async def publish(session, case: CopyrightCase, task: Task) -> None:
    require_confirmations(case, "drafts")
    verify_sources(task_dir(task.id) / "workspace", case.data["inventory"])
    errors = Registration.model_validate(case.data["registration"]).blockers() + draft_blockers(task, case)
    if errors:
        raise ValueError("；".join(errors))
    _, screenshots, fingerprint = validate_runtime(task)
    batch = CopyrightBatch(id=new_id(), task_id=task.id, revision=case.revision)
    session.add(batch)
    await session.commit()
    batch_dir = task_dir(task.id) / "copyright" / "batches" / batch.id
    try:
        render = asyncio.create_task(asyncio.to_thread(build_package, task, copy.deepcopy(case.data), batch_dir, screenshots))
        try:
            manifest = await asyncio.shield(render)
        except asyncio.CancelledError:
            # A filesystem writer must finish before task deletion removes its directory.
            await asyncio.gather(render, return_exceptions=True)
            batch.status = "failed"
            batch.error = "生成中断，本批次未发布"
            await session.commit()
            raise
        from .api import load_task
        latest = await load_task(session, task.id)
        if runtime_evidence(latest)[2] != fingerprint:
            raise ValueError("生成期间功能或截图发生变化，本批次未发布，请重新生成草稿")
        verify_sources(task_dir(task.id) / "workspace", case.data["inventory"])
        for item in manifest:
            artifact = Artifact(id=new_id(), task_id=task.id, kind=item["kind"], path=item["path"], mime_type=mimetypes.guess_type(item["path"])[0] or "application/octet-stream", size=item["size"])
            session.add(artifact)
            item["artifact_id"] = artifact.id
        batch.manifest = manifest
        batch.status = "published"
        latest.status = "completed"
        case.progress = "全部正式材料已生成并通过校验"
        await session.commit()
    except Exception:
        batch.status = "failed"
        batch.error = "生成或校验失败；上一批正式材料保持可用"
        await session.commit()
        raise


async def run_job(task_id: str, operation: str) -> None:
    async with lock_for(task_id), database.SessionLocal() as session:
        from .api import load_task
        case = await session.get(CopyrightCase, task_id)
        if not case:
            return
        try:
            task = await load_task(session, task_id)
            await {"analyze": analyze, "draft": generate_drafts, "review": review_drafts, "publish": publish}[operation](session, case, task)
            case.status = "idle"
            case.error = None
        except asyncio.CancelledError:
            case.status = "failed"
            case.error = "生成已中断，已完成阶段已保留，可重试"
            await session.commit()
            raise
        except Exception as exc:
            case.status = "failed"
            case.error = redact(str(exc))[:1500] if isinstance(exc, (ValueError, RuntimeError)) else "生成失败，请检查模型连接、字体和文档转换环境后重试"
        await session.commit()
        await broker.publish(task_id, "copyright", {"status": case.status, "error": case.error})


def schedule(task_id: str, operation: str) -> None:
    job = asyncio.create_task(run_job(task_id, operation))
    JOBS[task_id] = job
    job.add_done_callback(lambda done: JOBS.pop(task_id, None) if JOBS.get(task_id) is done else None)


async def recover() -> None:
    async with database.SessionLocal() as session:
        await session.execute(update(CopyrightCase).where(CopyrightCase.status == "running").values(status="failed", error="服务重启中断生成，可从已保存阶段重试"))
        await session.execute(update(CopyrightBatch).where(CopyrightBatch.status == "generating").values(status="failed", error="服务重启中断生成，本批次未发布"))
        await session.commit()


async def shutdown() -> None:
    jobs = list(JOBS.values())
    for job in jobs:
        job.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)


async def stop_task(task_id: str) -> None:
    job = JOBS.get(task_id)
    if job:
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
