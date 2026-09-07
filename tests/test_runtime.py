from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from manual_generator.browser_checks import page_problem
from manual_generator.runtime_discovery import detect_runtime
from manual_generator.runtime_driver import DockerRuntime, dockerfile
from manual_generator.runtime_schemas import Service, ordered_ids


@pytest.mark.asyncio
async def test_upload_binding_and_unchanged_hash(tmp_path):
    from manual_generator.explorer import Explorer
    from manual_generator.runtime_schemas import FeatureExecution
    from manual_generator.schemas import browser_action_adapter
    explorer = Explorer(None, None)
    explorer.task = SimpleNamespace(start_url="http://app/")
    action = browser_action_adapter.validate_python({"action": "upload", "file_id": "foreign", "target": {"selector": "input"}})
    with pytest.raises(ValueError, match="跨任务"):
        await explorer._execute(SimpleNamespace(url="http://app/"), action, tmp_path, "f", 1)
    explorer.rule = FeatureExecution(file_ids=["foreign"])
    file = tmp_path / "input"
    file.write_bytes(b"changed")
    explorer.file_records["foreign"] = SimpleNamespace(path=str(file), sha256="old")
    with pytest.raises(ValueError, match="未确认站点"):
        await explorer._execute(SimpleNamespace(url="http://external/"), action, tmp_path, "f", 1)
    with pytest.raises(ValueError, match="已变化"):
        await explorer._execute(SimpleNamespace(url="http://app/"), action, tmp_path, "f", 1)


@pytest.mark.asyncio
async def test_failed_response_does_not_verify():
    from manual_generator.explorer import Explorer
    from manual_generator.runtime_schemas import FeatureExecution, SuccessResponse
    explorer = Explorer(None, None)
    explorer.task = SimpleNamespace(start_url="http://app/")
    explorer.rule = FeatureExecution(success_response=SuccessResponse(path="/upload"))
    for status, data in [(500, {"ok": True}), (200, {"ok": False}), (200, [])]:
        await explorer._response(SimpleNamespace(url="http://app/upload", status=status, request=SimpleNamespace(method="POST"), json=AsyncMock(return_value=data)))
        assert not explorer.responses


@pytest.mark.asyncio
async def test_completed_download_not_repeated(tmp_path):
    from manual_generator.explorer import Explorer
    from manual_generator.schemas import browser_action_adapter
    explorer = Explorer(None, None)
    explorer.downloaded = True
    result, screenshot = await explorer._execute(None, browser_action_adapter.validate_python({"action": "download", "target": {"text": "Download"}}), tmp_path, "f", 1)
    assert "下载已完成" in result and screenshot is None


@pytest.mark.asyncio
async def test_download_missing_confirmation(tmp_path):
    from manual_generator.explorer import Explorer
    from manual_generator.schemas import browser_action_adapter
    explorer = Explorer(None, None)
    with pytest.raises(ValueError, match="未确认"):
        await explorer._execute(None, browser_action_adapter.validate_python({"action": "download", "target": {"text": "Download"}}), tmp_path, "f", 1)


@pytest.mark.asyncio
async def test_upload_selection_is_not_success(tmp_path, monkeypatch):
    import hashlib

    from manual_generator.explorer import Explorer
    from manual_generator.runtime_schemas import FeatureExecution
    from manual_generator.schemas import browser_action_adapter
    monkeypatch.setattr("manual_generator.explorer.asyncio.sleep", AsyncMock())
    explorer = Explorer(None, None)
    explorer.task = SimpleNamespace(start_url="http://app/")
    explorer.rule = FeatureExecution(file_ids=["file"])
    path = tmp_path / "input.csv"
    path.write_bytes(b"a,b")
    explorer.file_records["file"] = SimpleNamespace(path=str(path), sha256=hashlib.sha256(b"a,b").hexdigest(), name="input.csv", media_type="text/csv")
    explorer._locator = lambda *_: SimpleNamespace(set_input_files=AsyncMock())
    action = browser_action_adapter.validate_python({"action": "upload", "file_id": "file", "target": {"selector": "input"}})
    with pytest.raises(ValueError, match="未验证到上传成功"):
        await explorer._execute(SimpleNamespace(url="http://app/"), action, tmp_path, "f", 1)
    assert not explorer.uploaded


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    'services: {web: {image: nginx, privileged: true}}',
    'services: {web: {image: nginx, env_file: /app/.env}}',
    'services: {web: {image: nginx, volumes: ["/etc:/host"]}}',
    'services: {web: {image: "${PRIVATE_IMAGE}"}}',
    'services: {web: {image: nginx}}\nvolumes: {production: {external: true}}',
])
async def test_compose_rejects_external_resources(tmp_path, content):
    (tmp_path / "compose.yaml").write_text(content)
    runtime = object.__new__(DockerRuntime)
    runtime.command = AsyncMock()
    runtime.anchor, runtime.prefix, runtime.run_id = "anchor", "own", "run"
    runtime.task_id = "task"
    runtime.private_volume = None
    with pytest.raises(ValueError):
        await runtime.start_compose(Service(id="app", runtime="compose", dockerfile="compose.yaml"), tmp_path)
    runtime.command.assert_not_called()


@pytest.mark.parametrize("backend,manifest,contents,entry", [
    ("go", "go.mod", "module example.org/test\n\ngo 1.22", "main.go"),
    ("fastapi", "requirements.txt", "fastapi\nuvicorn", "main.py"),
    ("express", "package.json", '{"dependencies":{"express":"4.0.0"},"scripts":{"start":"node app.js"}}', "app.js"),
])
def test_nested_backend_frontend_detection(tmp_path, backend, manifest, contents, entry):
    root = tmp_path / "outer" / "project"
    back, front = root / "backend", root / "frontend"
    back.mkdir(parents=True)
    front.mkdir()
    (back / manifest).write_text(contents)
    (back / entry).write_text("")
    (front / "package.json").write_text('{"dependencies":{"vite":"6"},"scripts":{"dev":"vite"}}')
    metadata = tmp_path / "__MACOSX"
    metadata.mkdir()
    (metadata / "package.json").write_text("{}")
    plan = detect_runtime(tmp_path)
    assert len(plan.services) == 2
    assert {s.framework for s in plan.services} == {backend, "vite"}
    frontend = next(s for s in plan.services if s.framework == "vite")
    assert frontend.depends_on
    assert plan.entry_service == frontend.id


def test_unknown_and_static_sources(tmp_path):
    assert detect_runtime(tmp_path).blockers
    (tmp_path / "index.html").write_text('<div id="root"></div><script src="/src/main.tsx"></script>')
    assert not detect_runtime(tmp_path).services
    (tmp_path / "index.html").write_text("<h1>Actual site</h1>")
    plan = detect_runtime(tmp_path)
    assert plan.services[0].runtime == "static"
    assert plan.page_text == "Actual site"


@pytest.mark.parametrize("text,html,failures", [
    ("Directory listing for /", "", []),
    ("", '<div id="root"></div>', []),
    ("App", "<vite-error-overlay>", []),
    ("App", "", ["/assets/missing.js"]),
])
def test_false_readiness(text, html, failures):
    assert page_problem(text, html, failures)


def test_dependencies_and_templates():
    assert ordered_ids({"download": ["upload"], "upload": []}) == ["upload", "download"]
    with pytest.raises(ValueError, match="循环"):
        ordered_ids({"a": ["b"], "b": ["a"]})
    with pytest.raises(ValueError, match="不存在"):
        ordered_ids({"a": ["b"]})
    for path in ("../outside", "/tmp", "ok\nRUN bad"):
        with pytest.raises(ValueError):
            Service(id="app", runtime="go", directory=path)
    with pytest.raises(ValueError, match="密钥"):
        Service(id="app", runtime="go", environment={"API_KEY": "secret"})
    value = dockerfile(Service(id="app", runtime="go", directory=".service", command=["/app/server"]))
    assert '/workspace/.service' in value
    assert 'RUN ["go","build"' in value


@pytest.mark.asyncio
async def test_cleanup_continues_after_failure(tmp_path):
    runtime = object.__new__(DockerRuntime)
    runtime.processes, runtime.readers, runtime.compose_files = set(), [], []
    runtime.attached, runtime.network_created = False, True
    runtime.private_volume = None
    runtime.images = []
    runtime.network, runtime.containers = "owned-net", ["anchor", "app"]
    runtime.command = AsyncMock(side_effect=[RuntimeError("gone"), "", ""])
    await runtime.close()
    assert [call.args[0] for call in runtime.command.call_args_list] == [
        ["rm", "-f", "app"], ["rm", "-f", "anchor"], ["network", "rm", "owned-net"]]


@pytest.mark.asyncio
async def test_revision_files_and_legacy_preservation(tmp_path, monkeypatch):
    from manual_generator import database
    from manual_generator.config import get_settings
    from manual_generator.main import app
    from manual_generator.models import Feature, Task

    monkeypatch.setenv("MANUAL_GENERATOR_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    engine = database.create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(database, "SessionLocal", database.async_sessionmaker(engine, expire_on_commit=False))
    async with engine.begin() as conn:
        await conn.run_sync(database.Base.metadata.create_all)
    async with database.SessionLocal() as session:
        task = Task(name="test", status="uploaded")
        session.add(task)
        await session.flush()
        feature = Feature(task_id=task.id, title="upload", selected=True)
        session.add(feature)
        await session.commit()
        identity, feature_id = task.id, feature.id
    workspace = tmp_path / "tasks" / identity / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "index.html").write_text("<h1>Test</h1>")
    base = f"/api/tasks/{identity}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.get(base + "/runtime-plan")).json()
        assert state["revision"] == 1
        assert (await client.post(base + "/runtime-plan/confirm", json={"revision": 1})).status_code == 200
        upload = await client.post(base + "/test-files", data={"purpose": "synthetic input", "revision": 1}, files={"file": ("input.csv", b"a,b\n1,2")})
        assert upload.status_code == 200, upload.text
        file_id = upload.json()["id"]
        assert (await client.get(f"/api/tasks/other/test-files/{file_id}")).status_code == 404
        assert (await client.post(base + "/execution-config/confirm", json={"revision": 1})).status_code == 409
        assert (await client.get(base + "/runtime-plan")).json()["confirmations"] == {}
        execution = {"features": {feature_id: {"file_ids": [file_id], "success_response": {"path": "/upload"}}}}
        saved = await client.put(base + "/execution-config", json={"revision": 2, "value": execution})
        assert saved.status_code == 200
        for stage in ("runtime-plan", "execution-config"):
            result = await client.post(base + "/" + stage + "/confirm", json={"revision": 3})
            assert result.status_code == 200, result.text
        (workspace / "index.html").write_text("<h1>Changed</h1>")
        assert not any((await client.get(base + "/runtime-plan")).json()["confirmations"].values())
        assert (await client.post(base + "/run")).status_code == 409
        execution["features"][feature_id]["file_ids"] = ["other-task-file"]
        saved = await client.put(base + "/execution-config", json={"revision": 3, "value": execution})
        assert any("测试文件不属于" in b for b in saved.json()["blockers"])
    await engine.dispose()
    get_settings.cache_clear()
