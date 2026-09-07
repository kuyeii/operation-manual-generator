from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .config import Settings
from .database import SessionLocal
from .events import broker
from .explorer import Explorer, effective_browser_mode
from .llm import DeterministicClient, ModelOptions, OpenAICompatibleClient
from .models import ExecutionRecord, Feature, Run, RuntimeConfig, Step, Task
from .security import redact_secrets

RUNNING_STATES = {"queued", "installing", "starting", "authenticating", "exploring", "generating"}
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class TaskRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._jobs: dict[str, asyncio.Task] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._containers: dict[str, str] = {}
        self.credentials: dict[str, dict[str, str]] = {}

    async def recover_interrupted(self) -> None:
        from . import database
        async with database.SessionLocal() as session:
            from .runtime_driver import DockerRuntime
            records = (await session.scalars(select(ExecutionRecord).where(ExecutionRecord.status.in_(["pending", "starting", "exploring", "installing"])))).all()
            for record in records:
                runtime = DockerRuntime(record.task_id, record.run_id, self.settings, session, record)
                await runtime.recover_cleanup()
                record.status = "interrupted"
                record.data = {**record.data, "error": "服务重启，所属运行资源已尝试回收；请重新运行"}
            result = await session.execute(select(Task).where(Task.status.in_(RUNNING_STATES)))
            for task in result.scalars():
                task.status = "failed"
                task.error = "interrupted: 服务重启，任务未自动重放"
            await session.commit()

    async def shutdown(self) -> None:
        for job in self._jobs.values():
            job.cancel()
        await asyncio.gather(*self._jobs.values(), return_exceptions=True)
        for task_id in list(self._processes):
            await self._stop_process(task_id)
        for task_id in list(self._containers):
            await self._stop_process(task_id)

    def configure_credentials(self, task_id: str, values: dict[str, str]) -> None:
        self.credentials[task_id] = values

    def model_client(self) -> OpenAICompatibleClient | None:
        if not self.settings.llm_api_key:
            return None
        return OpenAICompatibleClient(
            ModelOptions(
                self.settings.llm_api_key,
                self.settings.llm_base_url,
                self.settings.llm_model,
                self.settings.llm_protocol,
            )
        )

    def start(self, task_id: str) -> None:
        existing = self._jobs.get(task_id)
        if existing and not existing.done():
            return
        self._jobs[task_id] = asyncio.create_task(self._run(task_id))

    async def cancel(self, task_id: str) -> None:
        job = self._jobs.get(task_id)
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        await self._stop_process(task_id)
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task:
                task.status = "cancelled"
                await session.commit()
        await broker.publish(task_id, "status", {"status": "cancelled"})

    async def pause(self, task_id: str) -> None:
        job = self._jobs.get(task_id)
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        await self._stop_process(task_id)
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task:
                task.status = "paused"
                await session.commit()
        await broker.publish(task_id, "status", {"status": "paused"})

    async def _run(self, task_id: str) -> None:
        async with self._lock:
            try:
                await self._execute(task_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._mark_failed(task_id, exc)

    async def _execute(self, task_id: str) -> None:
        async with SessionLocal() as session:
            if await session.get(RuntimeConfig, task_id):
                await self._execute_runtime(task_id, session)
                return
        task_dir = self.settings.data_dir / "tasks" / task_id
        async with SessionLocal() as session:
            task = await self._load_task(session, task_id)
            if not task or not task.launch_plan or not task.launch_plan.confirmed:
                raise ValueError("启动方案尚未确认")
            run = Run(task_id=task_id, status="installing", started_at=datetime.now(UTC))
            task.browser_mode = effective_browser_mode(task.browser_mode)
            session.add(run)
            task.status = "installing"
            await session.commit()
            await broker.publish(task_id, "status", {"status": task.status})

            await self._execute_legacy(task_id, task_dir, task, run, session)

    async def _execute_runtime(self, task_id, session):
        from .runtime_api import require_runtime_confirmed
        from .runtime_driver import DockerRuntime
        from .runtime_schemas import ExecutionConfig, RuntimePlan

        config = await require_runtime_confirmed(session, task_id)
        task = await self._load_task(session, task_id)
        task.browser_mode = effective_browser_mode(task.browser_mode)
        run = Run(task_id=task_id, status="installing", started_at=datetime.now(UTC))
        session.add(run)
        await session.flush()
        record = ExecutionRecord(task_id=task_id, run_id=run.id, revision=config.revision, data={"services": {}, "features": {}, "plan": config.plan, "execution": config.execution})
        session.add(record)
        task.status = "installing"
        for feature in task.features:
            if feature.selected:
                feature.status, feature.error = "pending", None
            for step in feature.steps:
                if step.screenshot:
                    step.screenshot.included = False
        await session.commit()
        runtime = DockerRuntime(task_id, run.id, self.settings, session, record)
        try:
            plan = RuntimePlan.model_validate(config.plan)
            url = await runtime.start(plan)
            task.start_url = url
            task.status = run.status = record.status = "exploring"
            await session.commit()
            explorer = Explorer(session, self._client(task_id), self.credentials.get(task_id), execution=ExecutionConfig.model_validate(config.execution), runtime_plan=plan, record=record)
            await explorer.explore_task(task, self.settings.data_dir / "tasks" / task_id)
            selected = [f for f in task.features if f.selected]
            completed = sum(f.status == "completed" for f in selected)
            task.status = "review_ready" if completed == len(selected) and selected else "partial_failed" if completed else "failed"
            task.error = None if task.status == "review_ready" else "部分功能未通过验证" if completed else "所选功能均未通过验证，请查看功能失败原因"
            run.status = record.status = "completed" if task.status == "review_ready" else task.status
            run.finished_at = datetime.now(UTC)
            await session.commit()
        except BaseException as exc:
            record.status = "interrupted" if isinstance(exc, asyncio.CancelledError) else "startup_failed" if task.status == "installing" else "readiness_failed"
            run.status = record.status
            run.finished_at = datetime.now(UTC)
            record.data = {**record.data, "error": redact_secrets(str(exc), (self.settings.llm_api_key or "",))[:5000]}
            await session.commit()
            raise
        finally:
            await runtime.close()
        await broker.publish(task_id, "status", {"status": task.status, "error": task.error})

    async def _execute_legacy(self, task_id, task_dir, task, run, session):
            cwd = (task_dir / "workspace" / task.launch_plan.working_directory).resolve()
            workspace = (task_dir / "workspace").resolve()
            if workspace != cwd and workspace not in cwd.parents:
                raise ValueError("工作目录超出任务源码范围")
            environment = self._environment(task_id, task.launch_plan.environment)
            install = self._target_command(task, task.launch_plan.install_command, task_dir)
            if install:
                await self._run_command(task_id, install, cwd, environment, wait=True)
            task.status = run.status = "starting"
            await session.commit()
            await broker.publish(task_id, "status", {"status": task.status})
            start = self._target_command(task, task.launch_plan.start_command, task_dir)
            await self._run_command(task_id, start, cwd, environment, wait=False)
            await self._wait_ready(task.start_url or "", timeout=90)
            task.status = run.status = "authenticating"
            await session.commit()
            await broker.publish(task_id, "status", {"status": task.status})
            task.status = run.status = "exploring"
            await session.commit()
            client = self._client(task_id)
            explorer = Explorer(session, client, self.credentials.get(task_id))
            await explorer.explore_task(task, task_dir)
            await self._stop_process(task_id)
            task.status = "review_ready"
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            await broker.publish(task_id, "status", {"status": task.status})

    async def _run_command(self, task_id: str, command: list[str], cwd: Path, env: dict[str, str], *, wait: bool) -> None:
        if not command or any("\x00" in part for part in command):
            raise ValueError("命令为空或包含非法字符")
        if not cwd.is_dir():
            raise ValueError(f"任务工作目录不存在：{cwd}")
        executable = command[0]
        found = shutil.which(executable, path=env.get("PATH")) if "/" not in executable else (cwd / executable).is_file()
        if not found:
            raise ValueError(f"任务运行环境缺少可执行程序：{executable}；请在生成器运行环境中安装，Docker任务还需要连接Docker服务")
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self._processes[task_id] = process
        reader = asyncio.create_task(self._read_output(task_id, process))
        if wait:
            return_code = await process.wait()
            await reader
            self._processes.pop(task_id, None)
            if return_code:
                raise RuntimeError(f"命令执行失败，退出码 {return_code}")

    async def _read_output(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        values = self.credentials.get(task_id, {})
        secrets = tuple(values.values()) + (self.settings.llm_api_key or "",)
        log_path = self.settings.data_dir / "tasks" / task_id / "task.log"
        assert process.stdout
        with log_path.open("a", encoding="utf-8") as log:
            while line := await process.stdout.readline():
                safe = redact_secrets(line.decode(errors="replace").rstrip(), secrets)
                log.write(safe + "\n")
                log.flush()
                await broker.publish(task_id, "log", {"message": safe})

    def _target_command(self, task: Task, command: list[str], task_dir: Path) -> list[str]:
        if task.launch_plan and task.launch_plan.project_type == "docker" and command[:1] == ["docker"]:
            name = f"manual-target-{task.id}"
            command = [name if part == "manual-generator-target" else part for part in command]
            if command[1:2] == ["run"]:
                if "--name" in command or any(part.startswith("--name=") for part in command):
                    raise ValueError("Docker任务容器名由系统分配，请移除启动命令中的--name")
                network = self.settings.docker_network_container
                if network:
                    if any(part in {"--network", "--net", "-P", "--publish-all"} or part.startswith(("--network=", "--net=")) for part in command):
                        raise ValueError("容器部署模式自动配置任务网络，请移除自定义网络参数")
                    # Share the browser's network namespace; no host ports need publishing.
                    filtered = command[:2]
                    index = 2
                    while index < len(command):
                        part = command[index]
                        if part == name:
                            filtered.extend(command[index:])
                            break
                        if part in {"-p", "--publish"}:
                            if index + 1 >= len(command):
                                raise ValueError("Docker端口映射缺少参数")
                            index += 2
                            continue
                        if part.startswith("--publish=") or (part.startswith("-p") and len(part) > 2):
                            index += 1
                            continue
                        filtered.append(part)
                        index += 1
                    command = filtered[:2] + ["--network", f"container:{network}"] + filtered[2:]
                command = command[:2] + ["--name", name] + command[2:]
                self._containers[task.id] = name
            return command
        if not command or not task.launch_plan or task.launch_plan.project_type != "python":
            return command
        runtime_python = (task_dir / "runtime" / ".venv" / "bin" / "python").resolve()
        if not runtime_python.exists():
            runtime_python.parent.parent.mkdir(parents=True, exist_ok=True)
            import subprocess

            subprocess.run([sys.executable, "-m", "venv", str(runtime_python.parent.parent)], check=True)
        runtime_pip = runtime_python.parent / "pip"
        replacements = {
            "python": str(runtime_python),
            "python3": str(runtime_python),
            "pip": str(runtime_pip),
            "pip3": str(runtime_pip),
        }
        return [replacements.get(part, part) for part in command]

    def _environment(self, task_id: str, configured: dict[str, str]) -> dict[str, str]:
        secret_keys = {"LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
        allowed = {key: value for key, value in os.environ.items() if key not in secret_keys}
        allowed.update(configured)
        allowed["PYTHONUNBUFFERED"] = "1"
        allowed["COMPOSE_PROJECT_NAME"] = f"manual_generator_{task_id.replace('-', '')[:12]}"
        return allowed

    def _client(self, task_id: str):
        return self.model_client() or DeterministicClient()

    async def _wait_ready(self, url: str, timeout: float) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        async with httpx.AsyncClient(follow_redirects=True) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(url, timeout=2)
                    if response.status_code < 500:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise TimeoutError(f"服务未在 {timeout:.0f} 秒内就绪：{url}")

    async def _stop_process(self, task_id: str) -> None:
        name = self._containers.pop(task_id, None)
        if name and shutil.which("docker"):
            cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(cleanup.wait(), timeout=15)
            except TimeoutError:
                cleanup.kill()
                await cleanup.wait()
        process = self._processes.pop(task_id, None)
        if not process or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), timeout=8)
        except (ProcessLookupError, TimeoutError):
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)

    async def _mark_failed(self, task_id: str, exc: Exception) -> None:
        await self._stop_process(task_id)
        async with SessionLocal() as session:
            task = await session.get(Task, task_id)
            if task:
                secrets = tuple(self.credentials.get(task_id, {}).values()) + (
                    self.settings.llm_api_key or "",
                )
                error = self._failure_message(task_id, exc, secrets)
                task.status = "failed"
                task.error = error
                result = await session.execute(
                    select(Run)
                    .where(Run.task_id == task_id, Run.status.in_(RUNNING_STATES))
                    .order_by(Run.started_at.desc())
                    .limit(1)
                )
                if run := result.scalar_one_or_none():
                    run.status = "failed"
                    run.error = error
                    run.finished_at = datetime.now(UTC)
                await session.commit()
                await broker.publish(task_id, "status", {"status": "failed", "error": task.error})

    def _failure_message(
        self, task_id: str, exc: Exception, secrets: tuple[str, ...]
    ) -> str:
        summary = redact_secrets(str(exc), secrets)
        log_path = self.settings.data_dir / "tasks" / task_id / "task.log"
        if not log_path.is_file():
            return summary
        lines = [
            ANSI_ESCAPE.sub("", line).strip()
            for line in log_path.read_text(errors="replace").splitlines()
            if line.strip()
        ]
        detail = "\n".join(lines[-8:])[-2000:]
        detail = redact_secrets(detail, secrets)
        return f"{summary}\n{detail}" if detail and detail not in summary else summary

    @staticmethod
    async def _load_task(session, task_id: str) -> Task | None:
        result = await session.execute(
            select(Task)
            .where(Task.id == task_id)
            .options(
                selectinload(Task.launch_plan),
                selectinload(Task.features)
                .selectinload(Feature.steps)
                .selectinload(Step.screenshot),
            )
        )
        return result.scalar_one_or_none()


runner: TaskRunner | None = None


def init_runner(settings: Settings) -> TaskRunner:
    global runner
    runner = TaskRunner(settings)
    return runner


def get_runner() -> TaskRunner:
    if not runner:
        raise RuntimeError("任务执行器尚未初始化")
    return runner
