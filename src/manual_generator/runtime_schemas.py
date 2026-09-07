from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Service(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")
    framework: str = "custom"
    directory: str = "."
    runtime: Literal["node", "python", "go", "static", "docker", "compose", "custom"]
    image: str = ""
    install: list[list[str]] = Field(default_factory=list)
    build: list[list[str]] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    port: int = Field(default=8080, ge=1, le=65535)
    health_path: str = "/"
    dockerfile: str = "Dockerfile"
    embed_frontend: str | None = None
    evidence: list[str] = Field(default_factory=list)

    @field_validator("directory", "dockerfile", "embed_frontend")
    @classmethod
    def safe_relative(cls, value):
        from pathlib import PurePosixPath
        if value is not None and (PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or any(c in value for c in "\n\r\x00\\")):
            raise ValueError("路径必须位于任务工作副本内")
        return value

    @field_validator("environment")
    @classmethod
    def non_secret_environment(cls, values):
        import re
        for key, value in values.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value or "\n" in value:
                raise ValueError("环境变量格式不合法")
            if re.search(r"(?:password|token|api_key|private_key|secret_key)", key, re.I):
                raise ValueError("密钥不能写入运行方案，请使用任务私密文件挂载")
        return values


class RuntimePlan(StrictModel):
    services: list[Service] = Field(default_factory=list, max_length=10)
    entry_service: str = ""
    entry_path: str = "/"
    page_text: str = ""
    page_selector: str = ""
    blockers: list[str] = Field(default_factory=list)

    @field_validator("entry_path")
    @classmethod
    def local_entry(cls, value):
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("入口必须是当前站点内路径")
        return value


class SuccessResponse(StrictModel):
    path: str = Field(pattern=r"^/")
    method: str = "POST"
    json_equals: dict = Field(default_factory=lambda: {"ok": True})
    capture_fields: list[str] = Field(default_factory=list, max_length=10)


class FeatureExecution(StrictModel):
    depends_on: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    success_text: str = ""
    success_response: SuccessResponse | None = None
    download_extension: str = ""


class ExecutionConfig(StrictModel):
    features: dict[str, FeatureExecution] = Field(default_factory=dict)


class Revision(StrictModel):
    revision: int = Field(ge=1)


class PlanUpdate(Revision):
    value: RuntimePlan


class ExecutionUpdate(Revision):
    value: ExecutionConfig


def ordered_ids(dependencies: dict[str, list[str]]) -> list[str]:
    result, visiting = [], set()
    def visit(key):
        if key in visiting:
            raise ValueError("依赖关系存在循环")
        if key not in dependencies:
            raise ValueError(f"依赖对象不存在：{key}")
        if key in result:
            return
        visiting.add(key)
        for dependency in dependencies[key]:
            visit(dependency)
        visiting.remove(key)
        result.append(key)
    for key in dependencies:
        visit(key)
    return result
