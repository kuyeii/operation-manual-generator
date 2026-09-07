from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .analyzer import (
    collect_feature_evidence,
    detect_launch_plan,
    evidence_payload,
    model_inventory_complete,
    static_features,
    validate_model_features,
)
from .config import Settings, get_settings
from .database import session_scope
from .events import broker
from .models import (
    Approval,
    Artifact,
    CopyrightCase,
    Feature,
    LaunchPlan,
    Screenshot,
    Step,
    Task,
    now,
)
from .reports import build_report
from .runner import get_runner
from .schemas import (
    ApprovalDecision,
    CredentialsRequest,
    ReviewRequest,
    ScreenshotUpdate,
)
from .security import ArchiveLimits, UnsafeArchiveError, safe_extract_zip

router = APIRouter(prefix="/api")


async def prepare_runtime_change(session: AsyncSession, task_id: str) -> None:
    from .copyright_service import invalidate

    task = await session.get(Task, task_id)
    if task and task.status in {"queued", "analyzing", "installing", "starting", "authenticating", "exploring", "generating"}:
        raise HTTPException(409, "任务运行中，不能修改配置")

    case = await session.get(CopyrightCase, task_id)
    if not case:
        return
    if case.status == "running":
        raise HTTPException(409, "软著资料正在生成，请完成后再修改功能或截图")
    if case.data.get("drafts"):
        invalidate(case, "drafts")


def update_features(session, task, items):
    existing = {f.id: f for f in task.features}
    by_title = {(f.title, f.entry_path): f for f in task.features}
    used = set()
    for position, item in enumerate(items):
        values = item.model_dump() if hasattr(item, "model_dump") else asdict(item)
        identity = values.pop("id", None)
        if identity and identity not in existing:
            raise HTTPException(422, "功能不属于当前任务")
        feature = existing.get(identity) if identity else by_title.get((values["title"], values["entry_path"]))
        if feature is None:
            feature = Feature(task_id=task.id, **values, position=position)
            session.add(feature)
        else:
            if feature.id in used:
                raise HTTPException(422, "功能重复")
            for key, value in values.items():
                setattr(feature, key, value)
            feature.position = position
            used.add(feature.id)
    for identity, feature in existing.items():
        if identity not in used:
            feature.selected = False


async def load_task(session: AsyncSession, task_id: str) -> Task:
    result = await session.execute(
        select(Task)
        .where(Task.id == task_id)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Task.launch_plan),
            selectinload(Task.features).selectinload(Feature.steps).selectinload(Step.screenshot),
            selectinload(Task.approvals),
            selectinload(Task.artifacts),
            selectinload(Task.copyright_batches),
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


def task_json(task: Task) -> dict:
    metadata = {item.get("artifact_id"): {"material": item["material"], "batch_id": batch.id, "formal": item["formal"]} for batch in task.copyright_batches for item in batch.manifest}
    return {
        "id": task.id,
        "name": task.name,
        "status": task.status,
        "browser_mode": task.browser_mode,
        "start_url": task.start_url,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "launch_plan": None if not task.launch_plan else {
            "project_type": task.launch_plan.project_type,
            "working_directory": task.launch_plan.working_directory,
            "install_command": task.launch_plan.install_command,
            "start_command": task.launch_plan.start_command,
            "environment": task.launch_plan.environment,
            "detected_files": task.launch_plan.detected_files,
            "confirmed": task.launch_plan.confirmed,
        },
        "features": [{
            "id": feature.id,
            "title": feature.title,
            "entry_path": feature.entry_path,
            "goal": feature.goal,
            "position": feature.position,
            "selected": feature.selected,
            "status": feature.status,
            "error": feature.error,
            "steps": [{
                "id": step.id,
                "position": step.position,
                "action": step.action,
                "target": step.target,
                "instruction": step.instruction,
                "url": step.url,
                "result": step.result,
                "screenshot": None if not step.screenshot else {
                    "id": step.screenshot.id,
                    "url": f"/api/tasks/{task.id}/screenshots/{step.screenshot.id}/file",
                    "included": step.screenshot.included,
                },
            } for step in feature.steps],
        } for feature in task.features],
        "approvals": [{"id": item.id, "action": item.action, "reason": item.reason, "status": item.status} for item in task.approvals],
        "artifacts": [{"id": item.id, "kind": item.kind, "name": Path(item.path).name, "size": item.size, "url": f"/api/tasks/{task.id}/artifacts/{item.id}", **metadata.get(item.id, {"material": "manual", "batch_id": None, "formal": False})} for item in task.artifacts if get_settings().pdf_enabled or item.kind.lower() != "pdf"],
    }


def can_generate_report(task: Task) -> bool:
    return any(
        feature.selected and feature.status == "completed" and feature.steps
        for feature in task.features
    )


def all_selected_features_completed(task: Task) -> bool:
    selected = [feature for feature in task.features if feature.selected]
    return bool(selected) and all(
        feature.status == "completed" and feature.steps for feature in selected
    )


@router.post("/tasks")
async def create_task(
    name: str = Form(...),
    source: UploadFile = File(...),
    session: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
):
    if not source.filename or not source.filename.lower().endswith(".zip"):
        raise HTTPException(400, "仅支持 ZIP 源码包")
    task = Task(name=name.strip() or Path(source.filename).stem)
    session.add(task)
    await session.flush()
    task_dir = settings.data_dir / "tasks" / task.id
    task_dir.mkdir(parents=True)
    archive = task_dir / "source.zip"
    size = 0
    try:
        with archive.open("wb") as output:
            while chunk := await source.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "上传文件超过大小限制")
                output.write(chunk)
        safe_extract_zip(archive, task_dir / "workspace", ArchiveLimits(settings.max_zip_files, settings.max_expanded_bytes))
    except Exception as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        await session.rollback()
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, UnsafeArchiveError):
            raise HTTPException(400, str(exc)) from exc
        raise
    await session.commit()
    return task_json(await load_task(session, task.id))


@router.get("/tasks")
async def list_tasks(session: AsyncSession = Depends(session_scope)):
    result = await session.execute(select(Task).order_by(Task.created_at.desc()))
    return [{"id": task.id, "name": task.name, "status": task.status, "updated_at": task.updated_at} for task in result.scalars()]


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, session: AsyncSession = Depends(session_scope)):
    return task_json(await load_task(session, task_id))


@router.post("/tasks/{task_id}/analyze")
async def analyze_task(task_id: str, session: AsyncSession = Depends(session_scope), settings: Settings = Depends(get_settings)):
    task = await load_task(session, task_id)
    await prepare_runtime_change(session, task_id)
    task.status = "analyzing"
    await session.commit()
    try:
        workspace = settings.data_dir / "tasks" / task.id / "workspace"
        preserve_plan = task.launch_plan is not None and task.launch_plan.confirmed
        plan = None if preserve_plan else await asyncio.to_thread(detect_launch_plan, workspace)
        evidence = await asyncio.to_thread(collect_feature_evidence, workspace)
        fallback_features = static_features(evidence)
        features = fallback_features
        analysis_notice = None
        model_client = get_runner().model_client()
        if model_client:
            try:
                proposals = await model_client.discover_features(evidence_payload(evidence))
                model_features = validate_model_features(proposals, evidence)
                if not model_inventory_complete(
                    proposals, evidence, model_features, fallback_features
                ):
                    raise ValueError("模型功能清单未完整覆盖本地识别结果")
                features = model_features
            except Exception:
                analysis_notice = "AI 功能识别不可用或结果无效，已使用静态识别结果。"
        else:
            analysis_notice = "系统模型未配置，已使用静态识别结果。"
        if plan:
            plan_data = asdict(plan)
            plan_data.pop("start_url")
            if task.launch_plan:
                for key, value in plan_data.items():
                    setattr(task.launch_plan, key, value)
                task.launch_plan.confirmed = False
            else:
                session.add(LaunchPlan(task_id=task.id, **plan_data))
        update_features(session, task, features)
        from .models import RuntimeConfig
        from .runtime_api import invalidate_config
        config = await session.get(RuntimeConfig, task_id)
        if config:
            invalidate_config(config)
        if plan:
            task.start_url = plan.start_url
        task.status = "awaiting_review"
        task.error = analysis_notice
        await session.commit()
    except Exception as exc:
        task.status, task.error = "failed", str(exc)
        await session.commit()
        raise HTTPException(400, str(exc)) from exc
    return task_json(await load_task(session, task.id))


@router.put("/tasks/{task_id}/review")
async def review_task(task_id: str, payload: ReviewRequest, session: AsyncSession = Depends(session_scope)):
    task = await load_task(session, task_id)
    if not task.launch_plan:
        raise HTTPException(409, "请先分析源码")
    await prepare_runtime_change(session, task_id)
    task.start_url = str(payload.start_url)
    task.browser_mode = payload.browser_mode
    for key, value in payload.launch_plan.model_dump().items():
        setattr(task.launch_plan, key, value)
    task.launch_plan.confirmed = True
    update_features(session, task, payload.features)
    from .models import RuntimeConfig
    from .runtime_api import invalidate_config
    config = await session.get(RuntimeConfig, task_id)
    if config:
        invalidate_config(config)
    task.status = "ready"
    task.error = None
    await session.commit()
    return task_json(await load_task(session, task.id))


@router.put("/tasks/{task_id}/credentials", status_code=204)
async def set_credentials(task_id: str, payload: CredentialsRequest, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    get_runner().configure_credentials(task_id, {"username": payload.username, "password": payload.password})


@router.post("/tasks/{task_id}/run", status_code=202)
async def run_task(task_id: str, session: AsyncSession = Depends(session_scope)):
    from .runtime_api import locks
    async with locks.setdefault(task_id, asyncio.Lock()):
        return await enqueue_task(task_id, session)


async def enqueue_task(task_id: str, session: AsyncSession):
    task = await load_task(session, task_id)
    from .runtime_api import require_runtime_confirmed
    try:
        config = await require_runtime_confirmed(session, task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not config:
        raise HTTPException(409, "请先确认运行方案和业务页面就绪条件")
    await prepare_runtime_change(session, task_id)
    for feature in task.features:
        if feature.status == "completed":
            feature.error = None
    task.status = "queued"
    task.error = None
    await session.commit()
    get_runner().start(task_id)
    return {"status": "queued"}


@router.post("/tasks/{task_id}/pause", status_code=202)
async def pause_task(task_id: str, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    await get_runner().pause(task_id)
    return {"status": "paused"}


@router.post("/tasks/{task_id}/resume", status_code=202)
async def resume_task(task_id: str, session: AsyncSession = Depends(session_scope)):
    return await run_task(task_id, session)


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_task(task_id: str, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    await get_runner().cancel(task_id)
    return {"status": "cancelled"}


@router.post("/tasks/{task_id}/approvals/{approval_id}")
async def decide_approval(task_id: str, approval_id: str, payload: ApprovalDecision, session: AsyncSession = Depends(session_scope)):
    approval = await session.get(Approval, approval_id)
    if not approval or approval.task_id != task_id:
        raise HTTPException(404, "审批不存在")
    approval.status = "approved" if payload.approved else "rejected"
    approval.resolved_at = now()
    await session.commit()
    return {"status": approval.status}


@router.post("/tasks/{task_id}/features/{feature_id}/retry", status_code=202)
async def retry_feature(task_id: str, feature_id: str, session: AsyncSession = Depends(session_scope)):
    feature = await session.get(Feature, feature_id)
    if not feature or feature.task_id != task_id:
        raise HTTPException(404, "功能不存在")
    await prepare_runtime_change(session, task_id)
    from .runtime_api import require_runtime_confirmed
    try:
        await require_runtime_confirmed(session, task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    feature.status, feature.error, feature.selected = "pending", None, True
    task = await session.get(Task, task_id)
    if task:
        task.status = "queued"
        task.error = None
    await session.commit()
    get_runner().start(task_id)
    return {"status": "queued"}


@router.put("/tasks/{task_id}/screenshots/{screenshot_id}")
async def update_screenshot(task_id: str, screenshot_id: str, payload: ScreenshotUpdate, session: AsyncSession = Depends(session_scope)):
    screenshot = await session.get(Screenshot, screenshot_id)
    if not screenshot:
        raise HTTPException(404, "截图不存在")
    feature_task = await session.scalar(select(Feature.task_id).join(Step).where(Step.id == screenshot.step_id))
    if feature_task != task_id:
        raise HTTPException(404, "截图不存在")
    if screenshot.included != payload.included:
        await prepare_runtime_change(session, task_id)
    screenshot.included = payload.included
    await session.commit()
    return {"included": screenshot.included}


@router.get("/tasks/{task_id}/screenshots/{screenshot_id}/file")
async def screenshot_file(task_id: str, screenshot_id: str, session: AsyncSession = Depends(session_scope)):
    screenshot = await session.get(Screenshot, screenshot_id)
    if not screenshot:
        raise HTTPException(404, "截图不存在")
    feature_task = await session.scalar(
        select(Feature.task_id).join(Step).where(Step.id == screenshot.step_id)
    )
    if feature_task != task_id:
        raise HTTPException(404, "截图不存在")
    return FileResponse(screenshot.path, media_type="image/png")


@router.post("/tasks/{task_id}/report")
async def generate_report(task_id: str, session: AsyncSession = Depends(session_scope), settings: Settings = Depends(get_settings)):
    task = await load_task(session, task_id)
    if not can_generate_report(task):
        raise HTTPException(409, "至少完成一项功能探索并记录操作步骤后才能生成报告")
    task.status = "generating"
    await session.commit()
    try:
        docx = await asyncio.to_thread(build_report, task, settings.data_dir / "tasks" / task.id)
    except Exception as exc:
        task.status = "failed"
        task.error = f"报告生成失败：{exc}"
        await session.commit()
        raise HTTPException(500, task.error) from exc
    await session.execute(delete(Artifact).where(Artifact.task_id == task.id, Artifact.path == str(docx)))
    session.add(
        Artifact(
            task_id=task.id,
            kind="docx",
            path=str(docx),
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size=docx.stat().st_size,
        )
    )
    task.status = "completed" if all_selected_features_completed(task) else "review_ready"
    await session.commit()
    return task_json(await load_task(session, task.id))


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
async def download_artifact(task_id: str, artifact_id: str, session: AsyncSession = Depends(session_scope)):
    artifact = await session.get(Artifact, artifact_id)
    if not artifact or artifact.task_id != task_id:
        raise HTTPException(404, "产物不存在")
    return FileResponse(artifact.path, media_type=artifact.mime_type, filename=Path(artifact.path).name)


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    return StreamingResponse(broker.stream(task_id), media_type="text/event-stream")


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, session: AsyncSession = Depends(session_scope), settings: Settings = Depends(get_settings)):
    from .copyright_service import stop_task

    task = await load_task(session, task_id)
    await stop_task(task_id)
    await get_runner().cancel(task_id)
    await session.delete(task)
    await session.commit()
    shutil.rmtree(settings.data_dir / "tasks" / task_id, ignore_errors=True)
