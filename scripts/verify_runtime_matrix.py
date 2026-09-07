"""Exercise isolated Go, FastAPI and Express + Vite runtime templates on Linux Docker."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from playwright.async_api import async_playwright

from manual_generator.browser_checks import verify_page
from manual_generator.config import get_settings
from manual_generator.runtime_discovery import detect_runtime
from manual_generator.runtime_driver import DockerRuntime


async def main():
    settings = get_settings()
    results = []
    for kind in ("go", "python", "node"):
        task_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        root = settings.data_dir / "tasks" / task_id
        workspace = root / "workspace"
        backend, frontend = workspace / "backend", workspace / "frontend"
        backend.mkdir(parents=True)
        frontend.mkdir()
        fixtures = {
            "go": {"go.mod": "module example.org/fixture\n\ngo 1.22\n", "main.go": 'package main\nimport("net/http")\nfunc main(){http.HandleFunc("/",func(w http.ResponseWriter,r *http.Request){w.Header().Set("Content-Type","application/json");w.Write([]byte(`{"ok":true}`))});http.ListenAndServe(":8080",nil)}'},
            "python": {"requirements.txt": "fastapi==0.115.0\nuvicorn==0.30.6\n", "main.py": 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/api")\ndef probe(): return {"ok": True}\n@app.get("/")\ndef health(): return {"ok": True}\n'},
            "node": {"package.json": json.dumps({"scripts": {"start": "node app.js"}, "dependencies": {"express": "4.21.2"}}), "app.js": 'const app=require("express")();app.get("*",(req,res)=>res.json({ok:true}));app.listen(8080,"0.0.0.0");'},
        }
        port = 8001 if kind == "python" else 8080
        for name, value in fixtures[kind].items():
            (backend / name).write_text(value)
        (frontend / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}, "devDependencies": {"vite": "5.4.21"}}))
        (frontend / "vite.config.js").write_text(f'export default {{server:{{proxy:{{"/api":"http://127.0.0.1:{port}"}}}}}}')
        (frontend / "index.html").write_text('<h1>Runtime fixture</h1><p id="result">Pending</p><script type="module">fetch("/api").then(r=>r.json()).then(x=>document.querySelector("#result").textContent=x.ok?"Backend verified":"Failed")</script>')
        plan = detect_runtime(workspace)
        plan.page_selector = "#result:text-is('Backend verified')"
        record = SimpleNamespace(data={}, run_id=run_id)
        runtime = DockerRuntime(task_id, run_id, settings, AsyncMock(), record)
        try:
            url = await runtime.start(plan)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url)
                await verify_page(page, plan, root / "diagnostic.png")
                await page.screenshot(path=root / "verified.png")
                async with httpx.AsyncClient(trust_env=False) as client:
                    assert (await client.get(url + "api")).json() == {"ok": True}
                await browser.close()
            results.append({"kind": kind, "task": task_id, "run": run_id, "verified": True})
        finally:
            await runtime.close()
        print(json.dumps(results[-1]), flush=True)
    print(json.dumps({"matrix": results}), flush=True)


asyncio.run(main())
