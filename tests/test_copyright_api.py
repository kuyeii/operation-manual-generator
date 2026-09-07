from __future__ import annotations

import asyncio
import io
import zipfile

import httpx
import pytest
from asgi_lifespan import LifespanManager
from PIL import Image

from manual_generator.copyright_schemas import Business, Section
from manual_generator.copyright_service import ReviewResult
from manual_generator.models import Artifact, Feature, Screenshot, Step


@pytest.mark.asyncio
async def test_copyright_workflow_revision_isolation_recovery_and_atomic_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUAL_GENERATOR_DATA_DIR", str(tmp_path / "data"))
    from manual_generator import copyright_service as service
    from manual_generator import database
    from manual_generator.config import get_settings
    get_settings.cache_clear()
    await database.engine.dispose()
    database.engine = database.create_async_engine(get_settings().sqlite_url)
    database.SessionLocal = database.async_sessionmaker(database.engine, expire_on_commit=False)
    from manual_generator.main import app

    class ModelStub:
        async def structured_document(self, _instruction, context, schema):
            if issubclass(schema, Business):
                return schema(positioning="任务管理", industry="企业管理", users="操作员", purpose="管理任务", features=[{"title": "任务列表", "description": "查看任务名称与状态", "evidence_ids": ["main.py"]}], technical_notes=[{"title": "数据接口", "description": "list_tasks返回任务列表", "evidence_ids": ["main.py"]}])
            if schema is Section:
                return Section(title=context["chapter_title"], paragraphs=["任务列表展示任务名称和状态。"], evidence_ids=["main.py"])
            return ReviewResult(issues=[])

    monkeypatch.setattr(service, "model_client", ModelStub)
    def package_stub(task, data, directory, screenshots):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "application.txt"
        path.write_text("测试输出")
        return [{"path": str(path), "name": path.name, "kind": "txt", "material": "application", "formal": True, "size": path.stat().st_size}]
    monkeypatch.setattr(service, "build_package", package_stub)
    async with LifespanManager(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        async def create():
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("main.py", "def list_tasks():\n    return []\n")
            response = await client.post("/api/tasks", data={"name": "测试任务软件"}, files={"source": ("project.zip", stream.getvalue())})
            assert response.status_code == 200
            return response.json()["id"]
        task_id, other_id = await create(), await create()
        prefix = f"/api/tasks/{task_id}/copyright"
        async def state():
            return (await client.get(prefix)).json()
        async def generate(operation):
            current = await state()
            response = await client.post(f"{prefix}/generate/{operation}", json={"revision": current["revision"]})
            assert response.status_code == 202, response.text
            for _ in range(400):
                current = await state()
                if current["status"] != "running":
                    return current
                await asyncio.sleep(.01)
            pytest.fail("background job did not finish")
        async def confirm(stage):
            current = await state()
            response = await client.post(f"{prefix}/stages/{stage}/confirm", json={"revision": current["revision"]})
            assert response.status_code == 200, response.text
            return response.json()
        assert (await client.post(f"{prefix}/generate/publish", json={"revision": 1})).status_code == 409
        current = await generate("analyze")
        assert current["status"] == "idle", current
        assert current["data"]["business"]["positioning"] == "任务管理"
        old_revision = current["revision"]
        await confirm("business")
        profile = current["data"]["registration"]
        profile.update({key: "测试内容" for key, value in profile.items() if not value})
        profile.update(version="V1.0", short_name="", owner_type="法人", development_method="单独开发", originality="原创", completion_date="2026-01-01", publication_status="未发表", publication_date="", publication_place="", rights_acquisition="原始取得", ownership_notes="")
        response = await client.put(f"{prefix}/stages/registration", json={"revision": old_revision, "value": profile})
        assert response.status_code == 200, response.text
        assert (await client.post(f"{prefix}/stages/registration/confirm", json={"revision": old_revision})).status_code == 409
        await confirm("registration")
        await confirm("sources")
        # Missing runtime evidence cannot be silently replaced by fabricated screenshots.
        current = await generate("draft")
        assert current["status"] == "failed"
        root = service.task_dir(task_id)
        screenshot_path = root / "screenshots" / "screen.png"
        screenshot_path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (100, 60), "green").save(screenshot_path)
        async with database.SessionLocal() as session:
            feature = Feature(task_id=task_id, title="任务列表", goal="查看任务", position=0, selected=True, status="completed")
            step = Step(position=1, action="screenshot", instruction="查看任务列表", result="success")
            step.screenshot = Screenshot(path=str(screenshot_path), included=True)
            feature.steps = [step]
            session.add(feature)
            old_path = root / "old.docx"
            old_path.write_bytes(b"old-report")
            session.add(Artifact(task_id=task_id, kind="docx", path=str(old_path), mime_type="application/octet-stream", size=10))
            await session.commit()
        current = await generate("draft")
        assert current["status"] == "idle", current["error"]
        assert current["blockers"] == []
        assert len(current["data"]["drafts"]["design"]) == 8
        from manual_generator.models import CopyrightCase
        async with database.SessionLocal() as session:
            case = await session.get(CopyrightCase, task_id)
            case.data = {**case.data, "review_issues": ["样例模型误报"]}
            await session.commit()
        current = await state()
        assert "样例模型误报" in current["blockers"]
        rejected = await client.post(f"{prefix}/review-resolutions", json={"revision": current["revision"], "issue": "样例模型误报", "note": "太短"})
        assert rejected.status_code == 422
        resolved = await client.post(f"{prefix}/review-resolutions", json={"revision": current["revision"], "issue": "样例模型误报", "note": "已核对main.py中list_tasks的真实实现与文档一致，此项为验收模拟误报。"})
        assert resolved.status_code == 200, resolved.text
        assert not resolved.json()["blockers"]
        await confirm("drafts")
        current = await generate("publish")
        assert current["status"] == "idle", current["error"]
        assert current["batches"][0]["status"] == "published"
        url = current["batches"][0]["files"][0]["url"]
        assert (await client.get(url)).status_code == 200
        assert (await client.get(url.replace(task_id, other_id))).status_code == 404
        task = (await client.get(f"/api/tasks/{task_id}")).json()
        assert len(task["artifacts"]) == 2
        assert task["artifacts"][-1]["formal"]
        def failing_package(*args):
            raise ValueError("测试渲染失败")
        monkeypatch.setattr(service, "build_package", failing_package)
        current = await generate("publish")
        assert current["status"] == "failed"
        assert current["batches"][0]["status"] == "failed"
        assert (await client.get(url)).status_code == 200
        # A changed screenshot invalidates draft approval without mutating old packages.
        Image.new("RGB", (100, 60), "red").save(screenshot_path)
        assert any("截图已变化" in error for error in (await state())["blockers"])
        current = await generate("publish")
        assert current["status"] == "failed"
        assert "截图已变化" in current["error"]
        # A restart preserves user input and makes an interrupted operation retryable.
        async with database.SessionLocal() as session:
            case = await session.get(CopyrightCase, task_id)
            case.status = "running"
            await session.commit()
        await service.recover()
        assert (await state())["status"] == "failed"
        assert (await state())["data"]["registration"]["version"] == "V1.0"
        before = await state()
        screenshot_id = task["features"][0]["steps"][0]["screenshot"]["id"]
        response = await client.put(f"/api/tasks/{task_id}/screenshots/{screenshot_id}", json={"included": False})
        assert response.status_code == 200
        after = await state()
        assert after["revision"] > before["revision"]
        assert "drafts" not in after["confirmations"]
        assert "registration" in after["confirmations"]
    await database.engine.dispose()
