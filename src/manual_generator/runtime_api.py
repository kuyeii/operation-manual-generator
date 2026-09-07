from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .copyright_sources import digest
from .database import session_scope
from .models import ExecutionRecord, Feature, LaunchPlan, RuntimeConfig, Task, TestFile, new_id
from .runtime_discovery import detect_runtime
from .runtime_schemas import (
    ExecutionConfig,
    ExecutionUpdate,
    FeatureExecution,
    PlanUpdate,
    Revision,
    RuntimePlan,
    Service,
    ordered_ids,
)
from .schemas import FeatureInput
from .security import IGNORED_ARCHIVE_PARTS

router = APIRouter(prefix="/api/tasks/{task_id}", tags=["runtime"])
locks: dict[str, asyncio.Lock] = {}
ACTIVE = {"queued", "analyzing", "installing", "starting", "authenticating", "exploring", "generating"}
source_hash_cache: dict[str, dict] = {}


def task_directory(task_id: str) -> Path:
    return get_settings().data_dir / "tasks" / task_id


def within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("路径超出当前任务范围")
    return path


def source_fingerprint(task_id: str) -> str:
    root = task_directory(task_id) / "workspace"
    result, cache = [], {}
    previous = source_hash_cache.get(task_id, {})
    for current, directories, names in os.walk(root):
        directory = Path(current)
        directories[:] = sorted(n for n in directories if n not in IGNORED_ARCHIVE_PARTS and not n.startswith("._") and not (directory / n).is_symlink())
        for name in sorted(names):
            path = directory / name
            if name in IGNORED_ARCHIVE_PARTS or name.startswith("._") or path.is_symlink():
                continue
            stat = path.stat()
            relative = path.relative_to(root).as_posix()
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            prior = previous.get(relative)
            if prior and prior[0] == stamp:
                value = prior[1]
            else:
                with path.open("rb") as stream:
                    value = hashlib.file_digest(stream, "sha256").hexdigest()
            cache[relative] = (stamp, value)
            result.append((relative, value))
    source_hash_cache[task_id] = cache
    return digest(result)


async def get_config(session, task_id: str) -> RuntimeConfig:
    if not await session.get(Task, task_id):
        raise HTTPException(404, "任务不存在")
    config = await session.get(RuntimeConfig, task_id)
    if config is None:
        plan = detect_runtime(task_directory(task_id) / "workspace")
        legacy = await session.scalar(select(LaunchPlan).where(LaunchPlan.task_id == task_id))
        if legacy and legacy.confirmed and len(plan.services) == 1 and not plan.services[0].embed_frontend:
            service = plan.services[0]
            if legacy.project_type in {"node", "python", "static"} and legacy.project_type == service.runtime:
                try:
                    plan.services[0] = Service.model_validate({**service.model_dump(), "directory": legacy.working_directory, "command": legacy.start_command, "install": [legacy.install_command] if legacy.install_command else [], "environment": legacy.environment, "evidence": service.evidence + ["历史单服务方案；新运行前需确认页面就绪条件"]})
                except ValueError:
                    plan.blockers.append("历史方案包含不支持的路径或环境变量，请重新配置")
        config = RuntimeConfig(task_id=task_id, revision=1, plan=plan.model_dump(), execution={"features": {}}, confirmations={})
        session.add(config)
        await session.commit()
    return config


async def files_for(session, task_id: str):
    return list((await session.scalars(select(TestFile).where(TestFile.task_id == task_id))).all())


def file_json(file: TestFile) -> dict:
    return {"id": file.id, "name": file.name, "purpose": file.purpose, "size": file.size, "sha256": file.sha256, "media_type": file.media_type}


async def fingerprint(session, config) -> str:
    private = task_directory(config.task_id) / "runtime-private"
    private_hashes = [(p.relative_to(private).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(private.rglob("*")) if p.is_file() and not p.is_symlink()] if private.is_dir() else []
    return digest({"revision": config.revision, "plan": config.plan, "execution": config.execution, "files": [file_json(f) for f in await files_for(session, config.task_id)], "source": await asyncio.to_thread(source_fingerprint, config.task_id), "private": private_hashes})


async def blockers(session, config: RuntimeConfig) -> list[str]:
    plan = RuntimePlan.model_validate(config.plan)
    execution = ExecutionConfig.model_validate(config.execution)
    errors = list(plan.blockers)
    root = task_directory(config.task_id) / "workspace"
    try:
        ids = [s.id for s in plan.services]
        if not ids or len(ids) != len(set(ids)) or plan.entry_service not in ids:
            errors.append("请选择唯一的服务标识和有效入口服务")
        ordered_ids({s.id: s.depends_on for s in plan.services})
        ports = [s.port for s in plan.services]
        if len(set(ports)) != len(ports):
            errors.append("同一任务的服务端口不能重复")
        if not plan.page_text.strip() and not plan.page_selector.strip():
            errors.append("请填写业务页面就绪文本或定位器")
        for service in plan.services:
            path = within(root, service.directory)
            if not path.is_dir():
                errors.append(f"服务目录不存在：{service.directory}")
            if service.runtime not in {"docker", "compose"} and not service.command:
                errors.append(f"{service.id} 缺少启动命令")
            if service.embed_frontend:
                within(root, service.embed_frontend)
            if service.runtime in {"docker", "compose"} and not within(path, service.dockerfile).is_file():
                errors.append(f"部署文件不存在：{service.dockerfile}")
        features = list((await session.scalars(select(Feature).where(Feature.task_id == config.task_id))).all())
        feature_ids = {f.id for f in features}
        selected = {f.id for f in features if f.selected}
        ordered_ids({key: execution.features.get(key, FeatureExecution()).depends_on for key in feature_ids})
        files = {f.id: f for f in await files_for(session, config.task_id)}
        for key, value in execution.features.items():
            if key not in feature_ids:
                errors.append("功能配置引用了其他任务或已移除的功能")
            if key in selected and any(dep not in selected for dep in value.depends_on):
                errors.append("已选功能的前置功能必须同时选中")
            if value.file_ids and not (value.success_text or value.success_response):
                errors.append("上传功能必须配置成功提示或接口结果校验")
            for identity in value.file_ids:
                file = files.get(identity)
                if file is None:
                    errors.append("测试文件不属于当前任务或已删除")
                elif not Path(file.path).is_file() or hashlib.sha256(Path(file.path).read_bytes()).hexdigest() != file.sha256:
                    errors.append(f"测试文件已丢失或变化：{file.name}")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


async def require_runtime_confirmed(session, task_id: str) -> RuntimeConfig | None:
    config = await session.get(RuntimeConfig, task_id)
    if config:
        issues = await blockers(session, config)
        current = await fingerprint(session, config)
        if issues or any(config.confirmations.get(stage) != current for stage in ("plan", "execution")):
            raise ValueError("；".join(issues) or "运行方案或文件用途尚未确认，或确认已过期")
    return config


async def editable(session, task_id: str, revision: int | None = None):
    from .api import prepare_runtime_change
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status in ACTIVE:
        raise HTTPException(409, "任务运行中，不能修改运行配置")
    config = await get_config(session, task_id)
    if revision is not None and config.revision != revision:
        raise HTTPException(409, "配置已更新，请刷新后重试")
    await prepare_runtime_change(session, task_id)
    return config


def invalidate_config(config):
    config.revision += 1
    config.confirmations = {}


@router.get("/runtime-plan")
@router.get("/execution-config")
async def state(task_id: str, session: AsyncSession = Depends(session_scope)):
    config = await get_config(session, task_id)
    records = list((await session.scalars(select(ExecutionRecord).where(ExecutionRecord.task_id == task_id).order_by(ExecutionRecord.created_at.desc()).limit(10))).all())
    current = await fingerprint(session, config)
    return {"revision": config.revision, "plan": config.plan, "execution": config.execution, "confirmations": {k: v == current for k, v in config.confirmations.items()}, "blockers": await blockers(session, config), "files": [file_json(f) for f in await files_for(session, task_id)], "runs": [{"id": r.id, "run_id": r.run_id, "status": r.status, "revision": r.revision, "data": r.data, "created_at": r.created_at} for r in records]}


@router.put("/runtime-plan")
async def save_plan(task_id: str, payload: PlanUpdate, session: AsyncSession = Depends(session_scope)):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, payload.revision)
        config.plan = payload.value.model_dump()
        invalidate_config(config)
        await session.commit()
    return await state(task_id, session)


@router.post("/runtime-plan/detect")
async def rediscover(task_id: str, payload: Revision, session: AsyncSession = Depends(session_scope)):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, payload.revision)
        config.plan = detect_runtime(task_directory(task_id) / "workspace").model_dump()
        invalidate_config(config)
        await session.commit()
    return await state(task_id, session)


@router.put("/execution-config")
async def save_execution(task_id: str, payload: ExecutionUpdate, session: AsyncSession = Depends(session_scope)):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, payload.revision)
        config.execution = payload.value.model_dump()
        invalidate_config(config)
        await session.commit()
    return await state(task_id, session)


@router.post("/runtime-plan/confirm")
async def confirm_plan(task_id: str, payload: Revision, session: AsyncSession = Depends(session_scope)):
    return await confirm(task_id, payload, "plan", session)


@router.post("/execution-config/confirm")
async def confirm_execution(task_id: str, payload: Revision, session: AsyncSession = Depends(session_scope)):
    return await confirm(task_id, payload, "execution", session)


async def confirm(task_id, payload, stage, session):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, payload.revision)
        issues = await blockers(session, config)
        if issues:
            raise HTTPException(422, "；".join(issues))
        config.confirmations = {**config.confirmations, stage: await fingerprint(session, config)}
        await session.commit()
    return await state(task_id, session)


@router.post("/preflight")
async def preflight(task_id: str, session: AsyncSession = Depends(session_scope)):
    config = await get_config(session, task_id)
    issues = await blockers(session, config)
    if not shutil.which("docker"):
        issues.append("生成器环境缺少 Docker 客户端")
    return {"blockers": issues, "revision": config.revision}


@router.put("/features")
async def save_features(task_id: str, payload: list[FeatureInput], revision: int, session: AsyncSession = Depends(session_scope)):
    from .api import load_task, task_json, update_features
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, revision)
        task = await load_task(session, task_id)
        update_features(session, task, payload)
        invalidate_config(config)
        await session.commit()
    return task_json(await load_task(session, task_id))


@router.get("/runtime-runs/{record_id}/logs/{service_id}")
async def service_logs(task_id: str, record_id: str, service_id: str, session: AsyncSession = Depends(session_scope)):
    record = await session.get(ExecutionRecord, record_id)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", service_id):
        raise HTTPException(404, "服务不存在")
    if not record or record.task_id != task_id or service_id not in {"runtime", *record.data.get("services", {})}:
        raise HTTPException(404, "运行记录不存在")
    path = within(task_directory(task_id) / "runtime" / record.run_id, f"{service_id}.log")
    if not path.is_file():
        return {"text": ""}
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 64000))
        return {"text": stream.read().decode(errors="replace")}


@router.get("/test-files")
async def list_files(task_id: str, session: AsyncSession = Depends(session_scope)):
    await get_config(session, task_id)
    return [file_json(f) for f in await files_for(session, task_id)]


@router.post("/test-files")
async def upload_file(task_id: str, file: UploadFile = File(...), purpose: str = Form(...), revision: int = Form(...), session: AsyncSession = Depends(session_scope)):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, revision)
        name = Path((file.filename or "file").replace("\\", "/")).name[:240]
        if not purpose.strip():
            raise HTTPException(422, "请填写测试文件用途")
        if not name or name.startswith(".") or name.lower().endswith((".exe", ".sh", ".py", ".js", ".env")):
            raise HTTPException(422, "测试文件名称或类型不允许")
        settings = get_settings()
        total = sum(item.size for item in await files_for(session, task_id))
        identity = new_id()
        path = task_directory(task_id) / "test-files" / identity
        path.parent.mkdir(exist_ok=True)
        size, hash_value = 0, hashlib.sha256()
        try:
            with path.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_test_file_bytes or size + total > settings.max_test_files_bytes:
                        raise HTTPException(413, "测试文件超过单文件或任务总量限制")
                    output.write(chunk)
                    hash_value.update(chunk)
            if not size:
                raise HTTPException(422, "不能上传空文件")
            record = TestFile(id=identity, task_id=task_id, name=name, path=str(path), purpose=purpose[:1000], size=size, sha256=hash_value.hexdigest(), media_type=mimetypes.guess_type(name)[0] or "application/octet-stream")
            session.add(record)
            invalidate_config(config)
            await session.commit()
            return file_json(record)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


@router.get("/test-files/{file_id}")
async def download_file(task_id: str, file_id: str, session: AsyncSession = Depends(session_scope)):
    file = await session.get(TestFile, file_id)
    if not file or file.task_id != task_id:
        raise HTTPException(404, "测试文件不存在")
    return FileResponse(file.path, filename=file.name, media_type=file.media_type)


@router.delete("/test-files/{file_id}")
async def delete_file(task_id: str, file_id: str, revision: int, session: AsyncSession = Depends(session_scope)):
    async with locks.setdefault(task_id, asyncio.Lock()):
        config = await editable(session, task_id, revision)
        file = await session.get(TestFile, file_id)
        if not file or file.task_id != task_id:
            raise HTTPException(404, "测试文件不存在")
        await session.delete(file)
        invalidate_config(config)
        await session.commit()
        Path(file.path).unlink(missing_ok=True)
    return {"deleted": True}
