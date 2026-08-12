from datetime import datetime
from types import SimpleNamespace

import pytest

import manual_generator.runner as runner_module
from manual_generator.runner import TaskRunner


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, task, run):
        self.task = task
        self.run = run
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, *_):
        return self.task

    async def execute(self, *_):
        return FakeResult(self.run)

    async def commit(self):
        self.committed = True


def test_target_environment_excludes_system_model_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "system-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-secret")
    settings = SimpleNamespace(data_dir=tmp_path, llm_api_key="system-secret")

    environment = TaskRunner(settings)._environment("task-1", {})

    assert "LLM_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment


@pytest.mark.asyncio
async def test_mark_failed_closes_run_and_includes_redacted_log_tail(
    tmp_path, monkeypatch
) -> None:
    task_id = "task-1"
    log_dir = tmp_path / "tasks" / task_id
    log_dir.mkdir(parents=True)
    log_dir.joinpath("task.log").write_text(
        "building\nAPI_KEY=secret-key\nmain.go: missing go.sum entry\n"
    )
    settings = SimpleNamespace(data_dir=tmp_path, llm_api_key="secret-key")
    runner = TaskRunner(settings)
    task = SimpleNamespace(status="installing", error=None)
    run = SimpleNamespace(status="installing", error=None, finished_at=None)
    session = FakeSession(task, run)
    monkeypatch.setattr(runner_module, "SessionLocal", lambda: session)

    await runner._mark_failed(task_id, RuntimeError("命令执行失败，退出码 2"))

    assert task.status == "failed"
    assert run.status == "failed"
    assert isinstance(run.finished_at, datetime)
    assert "missing go.sum entry" in task.error
    assert "secret-key" not in task.error
    assert "[REDACTED]" in task.error
    assert run.error == task.error
    assert session.committed
