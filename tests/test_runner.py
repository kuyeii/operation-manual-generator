from datetime import datetime
from types import SimpleNamespace

import pytest

import manual_generator.runner as runner_module
from manual_generator.runner import TaskRunner


def test_docker_commands_use_task_identity_and_shared_network(tmp_path):
    settings = SimpleNamespace(docker_network_container="generator")
    runner = TaskRunner(settings)
    task = SimpleNamespace(id="test-id", launch_plan=SimpleNamespace(project_type="docker"))
    install = runner._target_command(task, ["docker", "build", "-t", "manual-generator-target", "."], tmp_path)
    assert install == ["docker", "build", "-t", "manual-target-test-id", "."]
    start = runner._target_command(task, ["docker", "run", "--rm", "-p", "8080:8080", "manual-generator-target", "-p", "application-argument"], tmp_path)
    assert start == ["docker", "run", "--name", "manual-target-test-id", "--network", "container:generator", "--rm", "manual-target-test-id", "-p", "application-argument"]
    assert runner._containers == {"test-id": "manual-target-test-id"}


def test_docker_local_mode_preserves_published_ports(tmp_path):
    runner = TaskRunner(SimpleNamespace(docker_network_container=""))
    task = SimpleNamespace(id="local-test", launch_plan=SimpleNamespace(project_type="docker"))
    command = runner._target_command(task, ["docker", "run", "--rm", "-p", "9000:8080", "manual-generator-target"], tmp_path)
    assert "9000:8080" in command
    assert "--network" not in command
    assert command[-1] == "manual-target-local-test"


@pytest.mark.parametrize("option", [["--name", "custom"], ["--network", "host"], ["--net=host"], ["-P"]])
def test_docker_rejects_conflicting_managed_options(tmp_path, option):
    runner = TaskRunner(SimpleNamespace(docker_network_container="generator"))
    task = SimpleNamespace(id="test", launch_plan=SimpleNamespace(project_type="docker"))
    with pytest.raises(ValueError):
        runner._target_command(task, ["docker", "run", *option, "manual-generator-target"], tmp_path)


@pytest.mark.asyncio
async def test_missing_executable_and_directory_have_actionable_errors(tmp_path):
    runner = TaskRunner(SimpleNamespace())
    with pytest.raises(ValueError, match="工作目录不存在"):
        await runner._run_command("test", ["unknown"], tmp_path / "missing", {}, wait=True)
    with pytest.raises(ValueError, match="缺少可执行程序：missing-test-command"):
        await runner._run_command("test", ["missing-test-command"], tmp_path, {"PATH": ""}, wait=True)


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
