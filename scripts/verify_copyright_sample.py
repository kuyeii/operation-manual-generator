"""Opt-in acceptance run: real configured LLM, real browser screenshots and real DOCX/PDF.

Only the bundled public sample and explicitly synthetic registration facts are used.
Run from the repository root with .venv/bin/python scripts/verify_copyright_sample.py.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(".playwright-browsers").resolve()))

import httpx
from asgi_lifespan import LifespanManager
from playwright.async_api import async_playwright

from manual_generator import database
from manual_generator.copyright_schemas import Registration
from manual_generator.copyright_service import task_dir
from manual_generator.main import app
from manual_generator.models import Feature, Screenshot, Step


async def main():
    sample = Path("samples/ticket-operations-demo").resolve()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(["node", "server.mjs"], cwd=sample, env={**os.environ, "PORT": str(port)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with httpx.AsyncClient() as health:
            for _ in range(60):
                try:
                    if (await health.get(f"http://127.0.0.1:{port}")).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise RuntimeError("样例服务启动失败")
        async with LifespanManager(app), httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", timeout=120) as client:
            if len(sys.argv) > 1:
                task_id = sys.argv[1]
            else:
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w") as archive:
                    for path in sample.rglob("*"):
                        if path.is_file() and "node_modules" not in path.parts:
                            archive.write(path, path.relative_to(sample))
                response = await client.post("/api/tasks", data={"name": "验收样例工单管理软件"}, files={"source": ("sample.zip", stream.getvalue())})
                response.raise_for_status()
                task_id = response.json()["id"]
            print("TASK_ID", task_id, flush=True)
            prefix = f"/api/tasks/{task_id}/copyright"
            async def state():
                response = await client.get(prefix)
                response.raise_for_status()
                return response.json()
            async def generate(operation):
                current = await state()
                response = await client.post(f"{prefix}/generate/{operation}", json={"revision": current["revision"]})
                response.raise_for_status()
                last = None
                for _ in range(2400):
                    current = await state()
                    if current["progress"] != last:
                        print(operation, current["progress"], flush=True)
                        last = current["progress"]
                    if current["status"] == "failed":
                        raise RuntimeError(current["error"])
                    if current["status"] != "running":
                        return current
                    await asyncio.sleep(.5)
                raise RuntimeError("验收任务超时")
            async def confirm(stage):
                current = await state()
                if stage in current["confirmations"]:
                    return
                response = await client.post(f"{prefix}/stages/{stage}/confirm", json={"revision": current["revision"]})
                if response.status_code != 200:
                    raise RuntimeError(response.text)
            current = await state()
            if not current["data"].get("business") or current["data"].get("analysis_schema_version") != 2:
                await generate("analyze")
            await confirm("business")
            current = await state()
            if "registration" not in current["confirmations"]:
                profile = Registration.model_validate(current["data"]["registration"]).model_dump()
                profile.update(software_name="验收样例工单管理软件", version="V1.0", owner_type="法人", owner_name="公开测试主体（非真实申请）", owner_country="中国", owner_region="测试地区", certificate_type="测试证件", certificate_number="TEST-ONLY-000000", completion_date="2026-01-01", development_method="单独开发", originality="原创", publication_status="未发表", publication_date="", publication_place="", rights_acquisition="原始取得", rights_scope="全部权利", development_hardware="Apple Silicon，16GB内存", runtime_hardware="普通计算机，8GB内存", development_os="macOS", development_tools="Node.js 22，文本编辑器", runtime_os="支持Node.js和浏览器的操作系统", supporting_software="Node.js 22，Chromium", technical_features="Node.js HTTP服务与浏览器页面交互，内存存储工单数据。")
                response = await client.put(f"{prefix}/stages/registration", json={"revision": current["revision"], "value": profile})
                response.raise_for_status()
                await confirm("registration")
            await confirm("sources")
            task = (await client.get(f"/api/tasks/{task_id}")).json()
            if not task["features"]:
                screenshots = task_dir(task_id) / "screenshots"
                screenshots.mkdir(exist_ok=True)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={"width": 1440, "height": 900})
                    await page.goto(f"http://127.0.0.1:{port}")
                    await page.get_by_text("门店打印机无法出单", exact=True).wait_for()
                    path = screenshots / "ticket-list.png"
                    await page.screenshot(path=str(path), full_page=False)
                    await browser.close()
                async with database.SessionLocal() as session:
                    feature = Feature(task_id=task_id, title="查看工单列表", goal="查看已有工单的标题、优先级、状态和负责人", selected=True, position=0, status="completed")
                    step = Step(position=1, action="screenshot", instruction="进入工单页面，查看已有工单及其状态、优先级和负责人。", result="success")
                    step.screenshot = Screenshot(path=str(path), included=True)
                    feature.steps = [step]
                    session.add(feature)
                    await session.commit()
            current = await state()
            if not current["data"].get("drafts"):
                current = await generate("draft")
            elif current["blockers"]:
                current = await generate("review")
            if current["blockers"]:
                raise RuntimeError("草稿待审核：" + "；".join(current["blockers"]))
            await confirm("drafts")
            current = await generate("publish")
            batch = current["batches"][0]
            assert batch["status"] == "published"
            for file in batch["files"]:
                response = await client.get(file["url"])
                assert response.status_code == 200 and len(response.content) == file["size"]
            print("ACCEPTANCE_OK", json.dumps({"task_id": task_id, "batch_id": batch["id"], "files": [file["name"] for file in batch["files"]]}, ensure_ascii=False), flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    asyncio.run(main())
