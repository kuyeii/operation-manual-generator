from __future__ import annotations

import asyncio
import json
import os
import shutil
from contextlib import suppress
from pathlib import Path, PurePosixPath

import httpx
import yaml

from .events import broker
from .runtime_api import task_directory, within
from .runtime_schemas import RuntimePlan, Service, ordered_ids
from .security import IGNORED_ARCHIVE_PARTS, redact_secrets


def dockerfile(service: Service) -> str:
    relative = PurePosixPath(service.directory)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("服务目录必须在任务工作副本内")
    directory = str(PurePosixPath("/workspace") / relative)
    lines = []
    if service.embed_frontend:
        lines += ["FROM node:22-bookworm-slim AS frontend", "COPY workspace /workspace", f"WORKDIR /workspace/{service.embed_frontend}", 'RUN ["npm","ci"]', 'RUN ["npm","run","build"]']
    image = service.image or {"node": "node:22-bookworm-slim", "python": "python:3.12-slim", "go": "golang:1.23-alpine", "static": "python:3.12-slim"}.get(service.runtime)
    if not image or any(c.isspace() for c in image):
        raise ValueError("请填写有效的运行时基础镜像")
    lines += [f"FROM {image} AS build", "COPY workspace /workspace", "WORKDIR " + json.dumps(directory)]
    for key, value in service.environment.items():
        if not key.replace("_", "").isalnum() or "\n" in value:
            raise ValueError("环境变量格式不合法")
        lines.append(f"ENV {key}={json.dumps(value)}")
    for command in service.install + service.build:
        if not command:
            raise ValueError("构建命令不能为空")
        lines.append("RUN " + json.dumps(command))
    if service.embed_frontend:
        lines.append(f"COPY --from=frontend /workspace/{service.embed_frontend}/dist ./dist")
    if service.runtime == "go":
        lines += ['RUN ["go","build","-o","/app/server","."]']
    lines.append("CMD " + json.dumps(service.command))
    return "\n".join(lines) + "\n"


class DockerRuntime:
    def __init__(self, task_id: str, run_id: str, settings, session, record):
        self.task_id, self.run_id = task_id, run_id
        self.settings, self.session, self.record = settings, session, record
        self.prefix = "mg-" + run_id
        self.network = self.prefix + "-net"
        self.anchor = self.prefix + "-netns"
        self.directory = task_directory(task_id) / "runtime" / run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.containers: list[str] = []
        self.images: list[str] = []
        self.attached = False
        self.network_created = False
        self.private_volume = None
        self.processes = set()
        self.readers = []
        self.compose_files = []
        self.label = f"manual-generator.run={run_id}"
        self.task_label = f"manual-generator.task={task_id}"

    async def command(self, args: list[str], service: str = "runtime", timeout: int = 900, capture=False):
        process = await asyncio.create_subprocess_exec("docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=4*1024*1024, env={k: v for k, v in os.environ.items() if k not in {"LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}})
        self.processes.add(process)
        output = []
        async def read():
            with (self.directory / f"{service}.log").open("a") as log:
                while line := await process.stdout.readline():
                    text = redact_secrets(line.decode(errors="replace"), (self.settings.llm_api_key or "",))
                    log.write(text)
                    log.flush()
                    output.append(text)
                    if len(output) > 200 and not capture:
                        output.pop(0)
                    if not capture:
                        await broker.publish(self.task_id, "service_log", {"service": service, "run_id": self.run_id, "message": text.rstrip()})
        reader = asyncio.create_task(read())
        try:
            await asyncio.wait_for(process.wait(), timeout)
            await reader
            if process.returncode:
                raise RuntimeError(f"{service} 执行失败（退出码 {process.returncode}）\n{''.join(output)[-5000:]}")
            return "".join(output)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(reader, return_exceptions=True)
            self.processes.discard(process)

    async def update(self, service, status, **detail):
        data = dict(self.record.data)
        data["services"] = {**data.get("services", {}), service: {"status": status, **detail}}
        self.record.data = data
        await self.session.commit()
        await broker.publish(self.task_id, "service", {"service": service, "status": status, "run_id": self.run_id, **detail})

    async def start(self, plan: RuntimePlan):
        if not shutil.which("docker"):
            raise ValueError("当前环境未安装Docker客户端")
        await self.command(["info", "--format", "{{.ServerVersion}}"], capture=True, timeout=20)
        await self.command(["network", "create", "--label", self.label, "--label", self.task_label, self.network])
        self.network_created = True
        private = task_directory(self.task_id) / "runtime-private"
        anchor_args = []
        if private.is_dir():
            self.private_volume = self.prefix + "-private"
            await self.command(["volume", "create", "--label", self.label, "--label", self.task_label, self.private_volume], capture=True)
            anchor_args = ["--mount", f"type=volume,source={self.private_volume},target=/run/test-private"]
        self.containers.append(self.anchor)
        await self.command(["run", "-d", "--name", self.anchor, "--label", self.label, "--label", self.task_label, "--network", self.network, *anchor_args, "busybox:latest", "sleep", "2147483647"])
        if self.private_volume:
            await self.command(["cp", str(private) + "/.", f"{self.anchor}:/run/test-private"], capture=True)
        generator = self.settings.docker_network_container
        if generator:
            await self.command(["network", "connect", self.network, generator])
            self.attached = True
        inspection = json.loads(await self.command(["inspect", self.anchor], capture=True))
        hostname = inspection[0]["NetworkSettings"]["Networks"][self.network]["IPAddress"]
        source = task_directory(self.task_id) / "workspace"
        snapshot = self.directory / "workspace"
        await asyncio.to_thread(shutil.copytree, source, snapshot, ignore=lambda parent, names: [n for n in names if n in IGNORED_ARCHIVE_PARTS or n.startswith("._") or (Path(parent) / n).is_symlink()])
        services = {s.id: s for s in plan.services}
        for identity in ordered_ids({s.id: s.depends_on for s in plan.services}):
            service = services[identity]
            await self.update(identity, "building")
            if service.runtime == "compose":
                await self.start_compose(service, snapshot)
            else:
                image = self.prefix + "-" + identity
                if service.runtime == "docker":
                    context = within(snapshot, service.directory)
                    config = within(context, service.dockerfile)
                else:
                    context = self.directory
                    config = self.directory / f"{identity}.Dockerfile"
                    config.write_text(dockerfile(service))
                self.images.append(image)
                await self.command(["build", "--label", self.label, "--label", self.task_label, "-f", str(config), "-t", image, str(context)], identity)
                name = image
                args = ["create", "--name", name, "--label", self.label, "--label", self.task_label, "--network", f"container:{self.anchor}"]
                if self.private_volume:
                    args += ["--mount", f"type=volume,source={self.private_volume},target=/run/test-private,readonly"]
                for key, value in service.environment.items():
                    args += ["-e", f"{key}={value}"]
                args += [image]
                self.containers.append(name)
                await self.command(args, identity)
                await self.command(["start", name], identity)
                self.readers.append(asyncio.create_task(self.command(["logs", "-f", name], identity, timeout=86400)))
            await self.update(identity, "starting")
            url = f"http://{hostname}:{service.port}{service.health_path}"
            await self.wait_ready(url, identity)
            await self.update(identity, "ready", url=url)
        entry = services[plan.entry_service]
        return f"http://{hostname}:{entry.port}{plan.entry_path}"

    async def wait_ready(self, url, identity):
        async with httpx.AsyncClient(follow_redirects=True, trust_env=False) as client:
            for _ in range(90):
                name = self.prefix + "-" + identity
                if name in self.containers:
                    state = json.loads(await self.command(["inspect", name], capture=True))
                    if not state[0]["State"]["Running"]:
                        raise RuntimeError(f"{identity} 服务提前退出；请查看该服务日志")
                try:
                    response = await client.get(url, timeout=2)
                    if response.status_code in range(200, 400):
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        raise TimeoutError(f"{identity} 服务未就绪：{url}")

    async def start_compose(self, service, snapshot):
        original = within(within(snapshot, service.directory), service.dockerfile)
        source_text = original.read_text()
        if "${" in source_text:
            raise ValueError("Compose环境插值需要先在已确认方案中明确填写，不能读取生成器环境")
        resolved = yaml.safe_load(source_text)
        if not isinstance(resolved, dict) or not isinstance(resolved.get("services"), dict):
            raise ValueError("Compose缺少服务定义")
        if any(resolved.get(key) for key in ("include", "secrets", "configs")):
            raise ValueError("Compose外部配置或密钥需要改用任务私密挂载")
        for name, volume in resolved.get("volumes", {}).items():
            if volume and (volume.get("external") or volume.get("driver_opts")):
                raise ValueError("Compose不能使用外部或宿主机存储卷")
            resolved["volumes"][name] = {"name": self.prefix + "-" + service.id + "-" + name, "labels": {"manual-generator.run": self.run_id}}
        for item in resolved.get("services", {}).values():
            if any(item.get(key) for key in ("privileged", "devices", "cap_add", "pid", "ipc", "extends", "env_file", "secrets", "configs", "volumes_from")):
                raise ValueError("Compose包含宿主机绑定、设备或特权配置，请先调整为任务内运行方案")
            for volume in item.get("volumes", []):
                source = volume.get("source", "") if isinstance(volume, dict) else volume.split(":")[0]
                if isinstance(volume, dict) and volume.get("type") != "volume" or source not in resolved.get("volumes", {}):
                    raise ValueError("Compose仅允许当前任务声明的命名卷")
            build = item.get("build")
            if build:
                build = {"context": build} if isinstance(build, str) else dict(build)
                if build.get("additional_contexts") or build.get("secrets") or build.get("ssh"):
                    raise ValueError("Compose构建不能引用外部上下文或密钥")
                context = within(snapshot, str(original.parent.relative_to(snapshot) / build.get("context", ".")))
                if not context.is_relative_to(snapshot):
                    raise ValueError("Compose构建上下文越界")
                within(context, build.get("dockerfile", "Dockerfile"))
                build["context"] = str(context)
                item["build"] = build
            item.pop("networks", None)
            item.pop("ports", None)
            item.pop("container_name", None)
            item["restart"] = "no"
            item["network_mode"] = "container:" + self.anchor
            item["extra_hosts"] = {name: "127.0.0.1" for name in resolved["services"]}
            item["labels"] = {**item.get("labels", {}), "manual-generator.run": self.run_id, "manual-generator.task": self.task_id}
            if self.private_volume:
                item.setdefault("volumes", []).append({"type": "volume", "source": "runtime-private", "target": "/run/test-private", "read_only": True})
        if self.private_volume:
            resolved.setdefault("volumes", {})["runtime-private"] = {"external": True, "name": self.private_volume}
        resolved.pop("networks", None)
        config = self.directory / f"{service.id}.compose.runtime.json"
        config.write_text(json.dumps(resolved))
        args = ["compose", "-p", self.prefix + "-" + service.id, "-f", str(config)]
        self.compose_files.append(args)
        await self.command([*args, "up", "-d", "--build"], service.id)
        self.readers.append(asyncio.create_task(self.command([*args, "logs", "-f", "--no-color"], service.id, timeout=86400)))

    async def close(self):
        for process in list(self.processes):
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*self.readers, return_exceptions=True)
        for args in self.compose_files:
            with suppress(Exception):
                await self.command([*args, "down", "--remove-orphans", "--volumes"], timeout=30)
        if self.attached:
            with suppress(Exception):
                await self.command(["network", "disconnect", "-f", self.network, self.settings.docker_network_container], timeout=20)
        for name in reversed(self.containers):
            with suppress(Exception):
                await self.command(["rm", "-f", name], timeout=20)
        if self.network_created:
            with suppress(Exception):
                await self.command(["network", "rm", self.network], timeout=20)
        if self.private_volume:
            with suppress(Exception):
                await self.command(["volume", "rm", self.private_volume], timeout=20)
        for image in self.images:
            with suppress(Exception):
                await self.command(["image", "rm", image], timeout=30)
        if hasattr(self, "record"):
            remaining = []
            try:
                remaining = (await self.command(["ps", "-aq", "--filter", "label=" + self.label], capture=True, timeout=20)).split()
            except Exception:
                remaining = ["无法检查Docker资源"]
            services = {key: {**value, "validated": value.get("status") == "ready", "status": "cleanup_pending" if remaining else "stopped"} for key, value in self.record.data.get("services", {}).items()}
            self.record.data = {**self.record.data, "services": services, "cleanup": {"complete": not remaining, "remaining_containers": remaining}}
            await self.session.commit()

    async def recover_cleanup(self):
        if not shutil.which("docker"):
            return
        with suppress(Exception):
            names = await self.command(["ps", "-aq", "--filter", "label=" + self.label], capture=True, timeout=20)
            for name in names.split():
                await self.command(["rm", "-f", name], timeout=20)
        with suppress(Exception):
            networks = await self.command(["network", "ls", "-q", "--filter", "label=" + self.label], capture=True, timeout=20)
            for network in networks.split():
                if self.settings.docker_network_container:
                    with suppress(Exception):
                        await self.command(["network", "disconnect", "-f", network, self.settings.docker_network_container], timeout=20)
                await self.command(["network", "rm", network], timeout=20)
        with suppress(Exception):
            volumes = await self.command(["volume", "ls", "-q", "--filter", "label=" + self.label], capture=True, timeout=20)
            for volume in volumes.split():
                await self.command(["volume", "rm", volume], timeout=20)
