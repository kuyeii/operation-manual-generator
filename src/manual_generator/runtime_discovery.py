from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .analyzer import IGNORE_PARTS
from .runtime_schemas import RuntimePlan, Service


def project_directories(root: Path) -> list[Path]:
    result = []
    for current, directories, _files in os.walk(root):
        path = Path(current)
        directories[:] = sorted(d for d in directories if d not in IGNORE_PARTS and not d.startswith(".") and not (path / d).is_symlink())
        if len(path.relative_to(root).parts) >= 4:
            directories.clear()
        result.append(path)
        if len(result) >= 200:
            break
    return result


def detect_runtime(workspace: Path) -> RuntimePlan:
    services, deployments, titles = [], [], []
    for root in project_directories(workspace):
        relative = root.relative_to(workspace).as_posix()
        identity = f"service-{len(services) + 1}"
        for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml", "Dockerfile"):
            if (root / name).is_file():
                text = (root / name).read_text(errors="replace")
                exposed = re.findall(r"(?im)^EXPOSE\s+(\d+)(?:/tcp)?(?:\s|$)", text)
                deployments.append(Service(id=f"deployment-{len(deployments) + 1}", directory=relative, runtime="docker" if name == "Dockerfile" else "compose", dockerfile=name, port=int(exposed[-1]) if exposed else 8080, evidence=[f"{relative}/{name}"]))
                break
        package = root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text())
            except (ValueError, OSError):
                continue
            dependencies = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            scripts = data.get("scripts", {})
            if "vite" in dependencies:
                framework, command, port = "vite", ["npm", "run", "dev", "--", "--host", "0.0.0.0"], 5173
            elif "next" in dependencies:
                framework, command, port = "next", ["npm", "run", "start", "--", "-H", "0.0.0.0"], 3000
            elif "@nestjs/core" in dependencies or "express" in dependencies:
                framework, command, port = "nestjs" if "@nestjs/core" in dependencies else "express", ["npm", "run", "start:prod" if "start:prod" in scripts else "start"], 8080
            else:
                framework, command, port = "node", (["npm", "run", "start"] if "start" in scripts else []), 3000
            install = ["npm", "ci"] if (root / "package-lock.json").exists() else ["npm", "install"]
            unsupported_lock = any((root / name).exists() for name in ("pnpm-lock.yaml", "yarn.lock"))
            service = Service(id=identity, runtime="node", framework=framework, directory=relative, image="node:22-bookworm-slim", install=[install] if not unsupported_lock else [], build=[["npm", "run", "build"]] if framework in {"next", "nestjs"} else [], command=command if not unsupported_lock else [], port=port, environment={"PORT": str(port)}, evidence=[f"{relative}/package.json"])
            services.append(service)
        elif (root / "go.mod").is_file():
            text = (root / "go.mod").read_text()
            version = re.search(r"(?m)^go\s+(\d+\.\d+)", text)
            main = (root / "main.go").read_text(errors="replace") if (root / "main.go").exists() else ""
            services.append(Service(id=identity, runtime="go", framework="go", directory=relative, image=f"golang:{version[1] if version else '1.23'}-alpine", command=["/app/server"], port=8080, health_path="/healthz" if '"/healthz"' in main else "/", evidence=[f"{relative}/go.mod"]))
        elif (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
            manifest = "requirements.txt" if (root / "requirements.txt").exists() else "pyproject.toml"
            text = (root / manifest).read_text(errors="replace").lower()
            entry = next((p for p in (root / "main.py", root / "app.py", root / "server.py") if p.exists()), None)
            framework, command = "python", []
            if (root / "manage.py").exists():
                framework, command = "django", ["python", "manage.py", "runserver", "0.0.0.0:8001", "--noreload"]
            elif entry and "fastapi" in text:
                framework, command = "fastapi", ["python", "-m", "uvicorn", f"{entry.stem}:app", "--host", "0.0.0.0", "--port", "8001"]
            elif entry and "flask" in text:
                framework, command = "flask", ["python", "-m", "flask", "--app", entry.stem, "run", "--host", "0.0.0.0", "--port", "8001"]
            services.append(Service(id=identity, runtime="python", framework=framework, directory=relative, image="python:3.12-slim", install=[["python", "-m", "pip", "install", "-r", manifest] if manifest == "requirements.txt" else ["python", "-m", "pip", "install", "."]], command=command, port=8001, evidence=[f"{relative}/{manifest}"]))
        elif (root / "index.html").is_file() and not any((parent / "package.json").exists() for parent in (root, *root.parents) if parent == workspace or workspace in parent.parents):
            html = (root / "index.html").read_text(errors="replace")
            if not re.search(r'(?:src|href)=["\'][^"\']*\.(?:tsx?|vue)(?:[?"\'])', html):
                services.append(Service(id=identity, runtime="static", framework="static", directory=relative, image="python:3.12-slim", command=["python", "-m", "http.server", "8001", "--bind", "0.0.0.0"], port=8001, evidence=[f"{relative}/index.html"]))
        for source in list(root.glob("*.tsx"))[:10] + list(root.glob("*.vue"))[:10] + list(root.glob("*.html"))[:10]:
            if source.stat().st_size < 100000:
                match = re.search(r"<h1[^>]*>\s*([^<{]+)</h1>", source.read_text(errors="replace"))
                if match:
                    titles.append(match[1].strip())
    plan = RuntimePlan(services=deployments or services, page_text=titles[0] if titles else "")
    if len(deployments) > 1:
        plan.blockers.append("发现多个部署入口，请选择需要运行的项目")
    if not plan.services:
        plan.blockers.append("未识别到可运行项目，请配置服务；不会自动启动文件目录服务")
    if not deployments:
        frontends = [s for s in services if s.framework in {"vite", "next"}]
        backends = [s for s in services if s not in frontends]
        if len(frontends) == 1 and len(backends) == 1:
            frontend, backend = frontends[0], backends[0]
            main = workspace / backend.directory / "main.go"
            if backend.runtime == "go" and main.exists() and "//go:embed dist/" in main.read_text():
                backend.embed_frontend = frontend.directory
                plan.services = [backend]
            else:
                frontend.depends_on = [backend.id]
                plan.entry_service = frontend.id
        elif len(services) > 1:
            plan.blockers.append("发现多个服务，请确认依赖关系、端口和入口服务")
    if plan.services and not plan.entry_service:
        plan.entry_service = plan.services[0].id
    for service in plan.services:
        if service.runtime not in {"docker", "compose"} and not service.command:
            plan.blockers.append(f"{service.directory} 的启动命令或包管理器需要人工确认")
    return plan
