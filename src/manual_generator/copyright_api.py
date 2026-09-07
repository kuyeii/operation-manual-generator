from __future__ import annotations

import copy
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .api import load_task
from .config import get_settings
from .copyright_documents import application_text
from .copyright_schemas import (
    FIELD_LABELS,
    STAGES,
    Business,
    Drafts,
    Registration,
    ReviewResolution,
    RevisionRequest,
    SourceSelection,
    StageUpdate,
)
from .copyright_service import (
    RULES,
    draft_blockers,
    invalidate,
    lock_for,
    require_confirmations,
    schedule,
    task_dir,
    validate_references,
)
from .copyright_sources import digest, source_pages, verify_sources
from .database import session_scope
from .models import CopyrightBatch, CopyrightCase

router = APIRouter(prefix="/api/tasks/{task_id}/copyright", tags=["copyright"])
Stage = Literal["business", "registration", "sources", "drafts"]
Operation = Literal["analyze", "draft", "review", "publish"]
SCHEMAS = {"business": Business, "registration": Registration, "sources": SourceSelection, "drafts": Drafts}


def detail(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "；".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors(include_input=False))
    return str(exc)


async def case_for(session: AsyncSession, task_id: str, revision: int) -> CopyrightCase:
    await load_task(session, task_id)
    case = await session.get(CopyrightCase, task_id, populate_existing=True)
    if not case:
        raise HTTPException(409, "请先分析软著资料")
    if case.status == "running":
        raise HTTPException(409, "当前生成阶段尚未完成")
    if case.revision != revision:
        raise HTTPException(409, "资料版本已更新，请刷新后重新审核")
    return case


@router.get("")
async def state(task_id: str, session: AsyncSession = Depends(session_scope)):
    task = await load_task(session, task_id)
    case = await session.get(CopyrightCase, task_id)
    batches = (await session.scalars(select(CopyrightBatch).where(CopyrightBatch.task_id == task_id).order_by(CopyrightBatch.created_at.desc()))).all()
    data = copy.deepcopy(case.data) if case else {}
    for key in ("analysis_checkpoint", "draft_checkpoint"):
        data.pop(key, None)
    blockers = []
    if case and data.get("drafts"):
        try:
            blockers = draft_blockers(task, case)
        except ValueError as exc:
            blockers = [detail(exc)]
    return {"revision": case.revision if case else 1, "status": case.status if case else "idle", "operation": case.operation if case else None, "progress": case.progress if case else "", "error": case.error if case else None, "data": data, "confirmations": case.confirmations if case else {}, "blockers": blockers, "field_labels": FIELD_LABELS, "rules": RULES, "batches": [{"id": batch.id, "revision": batch.revision, "status": batch.status, "error": batch.error, "created_at": batch.created_at, "files": [{**{key: item[key] for key in ("name", "kind", "material", "formal", "size")}, "url": f"/api/tasks/{task_id}/artifacts/{item['artifact_id']}"} for item in batch.manifest if get_settings().pdf_enabled or item["kind"].lower() != "pdf"]} for batch in batches]}


@router.put("/stages/{stage}")
async def save_stage(task_id: str, stage: Stage, payload: StageUpdate, session: AsyncSession = Depends(session_scope)):
    await case_for(session, task_id, payload.revision)
    async with lock_for(task_id):
        case = await case_for(session, task_id, payload.revision)
        try:
            if stage != "business":
                require_confirmations(case, STAGES[STAGES.index(stage) - 1])
            value = SCHEMAS[stage].model_validate(payload.value)
            if stage in {"business", "drafts"}:
                validate_references(value, case.data)
            if stage == "sources":
                source_pages(task_dir(task_id) / "workspace", case.data["inventory"], value.paths)
            if stage == "registration" and not case.data.get("business"):
                raise ValueError("请先完成业务分析")
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, detail(exc)) from exc
        invalidate(case, stage)
        data = copy.deepcopy(case.data)
        data[stage] = value.model_dump()
        if stage == "business":
            data["registration"].update({"purpose": value.purpose, "industry": value.industry, "main_functions": "；".join(feature.description for feature in value.features)})
        case.data = data
        await session.commit()
    return await state(task_id, session)


@router.post("/stages/{stage}/confirm")
async def confirm_stage(task_id: str, stage: Stage, payload: RevisionRequest, session: AsyncSession = Depends(session_scope)):
    await case_for(session, task_id, payload.revision)
    async with lock_for(task_id):
        case = await case_for(session, task_id, payload.revision)
        task = await load_task(session, task_id)
        try:
            if stage != "business":
                require_confirmations(case, STAGES[STAGES.index(stage) - 1])
            verify_sources(task_dir(task_id) / "workspace", case.data["inventory"])
            value = SCHEMAS[stage].model_validate(case.data.get(stage, {}))
            if stage == "business":
                validate_references(value, case.data)
            if stage == "registration" and (errors := value.blockers()):
                raise ValueError("；".join(errors))
            if stage == "sources":
                source_pages(task_dir(task_id) / "workspace", case.data["inventory"], value.paths)
            if stage == "drafts" and (errors := draft_blockers(task, case)):
                raise ValueError("；".join(errors))
        except (ValueError, KeyError) as exc:
            raise HTTPException(409, detail(exc)) from exc
        case.confirmations = {**case.confirmations, stage: {"revision": case.revision, "digest": digest(case.data[stage])}}
        await session.commit()
    return await state(task_id, session)


@router.post("/generate/{operation}", status_code=202)
async def generate(task_id: str, operation: Operation, payload: RevisionRequest, session: AsyncSession = Depends(session_scope)):
    # Reject immediately, rather than waiting behind a running generation's lock.
    task = await load_task(session, task_id)
    if task.status in {"analyzing", "queued", "installing", "starting", "authenticating", "exploring", "generating"}:
        raise HTTPException(409, "请等待当前项目分析、探索或报告任务完成")
    current = await session.get(CopyrightCase, task_id, populate_existing=True)
    if current and current.status == "running":
        raise HTTPException(409, "已有生成任务正在执行")
    async with lock_for(task_id):
        if not current:
            if operation != "analyze" or payload.revision != 1:
                raise HTTPException(409, "请先分析软著资料")
            current = CopyrightCase(task_id=task_id, revision=1, status="idle", data={}, confirmations={})
            session.add(current)
            await session.flush()
        case = await case_for(session, task_id, payload.revision)
        try:
            if operation in {"draft", "review"}:
                require_confirmations(case, "sources")
            elif operation == "publish":
                require_confirmations(case, "drafts")
            if operation == "analyze":
                invalidate(case, "business")
            elif operation in {"draft", "review"}:
                case.confirmations = {key: value for key, value in case.confirmations.items() if key != "drafts"}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        case.status = "running"
        case.operation = operation
        case.progress = "正在" + {"analyze": "分析源码", "draft": "生成草稿", "review": "复核草稿", "publish": "生成正式材料"}[operation]
        case.error = None
        await session.commit()
        schedule(task_id, operation)
        return {"status": "running", "revision": case.revision}


@router.get("/source-preview")
async def source_preview(task_id: str, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    case = await session.get(CopyrightCase, task_id)
    if not case or not case.data.get("sources"):
        raise HTTPException(409, "尚未选择源码")
    try:
        return source_pages(task_dir(task_id) / "workspace", case.data["inventory"], case.data["sources"]["paths"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/review-resolutions")
async def resolve_review(task_id: str, payload: ReviewResolution, session: AsyncSession = Depends(session_scope)):
    await case_for(session, task_id, payload.revision)
    async with lock_for(task_id):
        case = await case_for(session, task_id, payload.revision)
        try:
            require_confirmations(case, "sources")
            verify_sources(task_dir(task_id) / "workspace", case.data["inventory"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if payload.issue not in case.data.get("review_issues", []):
            raise HTTPException(409, "复核问题已更新，请刷新后核对")
        if len(payload.note.strip()) < 20:
            raise HTTPException(422, "请填写至少20字的源码核对依据及处理理由")
        data = copy.deepcopy(case.data)
        data.setdefault("review_resolutions", {})[digest(payload.issue)] = {"issue": payload.issue, "note": payload.note.strip(), "draft_digest": digest(data["drafts"])}
        case.data = data
        case.revision += 1
        case.confirmations = {key: value for key, value in case.confirmations.items() if key != "drafts"}
        await session.commit()
    return await state(task_id, session)


@router.get("/draft-download")
async def download_draft(task_id: str, session: AsyncSession = Depends(session_scope)):
    await load_task(session, task_id)
    case = await session.get(CopyrightCase, task_id)
    if not case or not case.data.get("registration"):
        raise HTTPException(409, "尚未生成草稿")
    data = case.data
    text = "# 软著材料草稿（非正式交付）\n\n## 申请填报信息\n\n" + application_text(data["registration"], data["inventory"]["source_lines"])
    for kind, title in (("manual", "操作手册"), ("design", "技术设计说明书")):
        text += f"\n# {title}\n"
        for section in data.get("drafts", {}).get(kind, []):
            text += f"\n## {section['title']}\n\n" + "\n\n".join(section["paragraphs"]) + "\n"
    text += "\n## 源码抽取清单\n\n" + json.dumps(data.get("sources", {}), ensure_ascii=False, indent=2)
    return Response(text, media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="copyright-drafts.md"'})
