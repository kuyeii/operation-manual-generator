from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager


def node_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(
            "demo/package.json",
            '{"scripts":{"dev":"vite"}}',
        )
        bundle.writestr(
            "demo/src/app.tsx",
            '<a href="/users">用户管理</a>',
        )
        symlink = zipfile.ZipInfo("demo/node_modules/.bin/jsesc")
        symlink.external_attr = 0o120777 << 16
        bundle.writestr(symlink, "../jsesc/bin/jsesc")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_analyze_and_review(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MANUAL_GENERATOR_DATA_DIR", str(tmp_path / "data"))
    from manual_generator.config import get_settings

    get_settings.cache_clear()
    import manual_generator.database as database

    await database.engine.dispose()
    database.settings = get_settings()
    database.engine = database.create_async_engine(database.settings.sqlite_url)
    database.SessionLocal = database.async_sessionmaker(database.engine, expire_on_commit=False)
    import manual_generator.api as api_module
    from manual_generator.main import app
    from manual_generator.models import Feature, Step, Task

    status_task = Task(name="demo")
    completed = Feature(title="计算", selected=True, status="completed")
    completed.steps = [Step(position=1, action="click", instruction="计算")]
    failed = Feature(title="上传", selected=True, status="failed")
    status_task.features = [completed, failed]
    assert not api_module.all_selected_features_completed(status_task)
    failed.status = "completed"
    failed.steps = [Step(position=1, action="finish", instruction="确认已上传")]
    assert api_module.all_selected_features_completed(status_task)

    class AnalysisRunnerStub:
        def model_client(self):
            return None

    class FailingModelClient:
        async def discover_features(self, _):
            raise TimeoutError("model timeout")

    class FailingAnalysisRunnerStub:
        def model_client(self):
            return FailingModelClient()

    monkeypatch.setattr(api_module, "get_runner", lambda: AnalysisRunnerStub())

    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            invalid = await client.post(
                "/api/tasks",
                data={"name": "损坏压缩包"},
                files={"source": ("broken.zip", b"not a zip", "application/zip")},
            )
            assert invalid.status_code == 400
            assert "ZIP 文件损坏" in invalid.json()["detail"]
            assert list((tmp_path / "data" / "tasks").iterdir()) == []

            created = await client.post(
                "/api/tasks",
                data={"name": "示例项目"},
                files={"source": ("source.zip", node_zip(), "application/zip")},
            )
            assert created.status_code == 200
            task = created.json()
            analyzed = await client.post(f"/api/tasks/{task['id']}/analyze")
            assert analyzed.status_code == 200, analyzed.text
            detail = analyzed.json()
            assert detail["launch_plan"]["project_type"] == "node"
            assert detail["error"] == "系统模型未配置，已使用静态识别结果。"
            monkeypatch.setattr(
                api_module, "get_runner", lambda: FailingAnalysisRunnerStub()
            )
            fallback = await client.post(f"/api/tasks/{task['id']}/analyze")
            assert fallback.status_code == 200
            fallback_detail = fallback.json()
            assert fallback_detail["status"] == "awaiting_review"
            assert fallback_detail["features"]
            assert fallback_detail["error"] == (
                "AI 功能识别不可用或结果无效，已使用静态识别结果。"
            )
            reviewed = await client.put(
                f"/api/tasks/{task['id']}/review",
                json={
                    "start_url": "http://127.0.0.1:5173",
                    "browser_mode": "headless",
                    "launch_plan": detail["launch_plan"],
                    "features": detail["features"],
                },
            )
            assert reviewed.status_code == 200
            assert reviewed.json()["status"] == "ready"
            assert reviewed.json()["error"] is None
            assert reviewed.json()["launch_plan"]["confirmed"] is True

            monkeypatch.setattr(api_module, "get_runner", lambda: AnalysisRunnerStub())
            reanalyzed = await client.post(f"/api/tasks/{task['id']}/analyze")
            assert reanalyzed.status_code == 200
            assert reanalyzed.json()["launch_plan"]["confirmed"] is True
            assert reanalyzed.json()["start_url"] == "http://127.0.0.1:5173/"

            class RunnerStub:
                def __init__(self) -> None:
                    self.started: list[str] = []

                def start(self, task_id: str) -> None:
                    self.started.append(task_id)

            runner = RunnerStub()
            monkeypatch.setattr(api_module, "get_runner", lambda: runner)
            started = await client.post(f"/api/tasks/{task['id']}/run")
            assert started.status_code == 202
            assert started.json()["status"] == "queued"
            assert runner.started == [task["id"]]

            report = await client.post(f"/api/tasks/{task['id']}/report")
            assert report.status_code == 409
            assert "至少完成一项功能探索" in report.json()["detail"]
