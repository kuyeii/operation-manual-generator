from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo


class UnsafeArchiveError(ValueError):
    pass


@dataclass(slots=True)
class ArchiveLimits:
    max_files: int = 50_000
    max_expanded_bytes: int = 5 * 1024**3


IGNORED_ARCHIVE_PARTS = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
    "__MACOSX",
    ".DS_Store",
}


def _validated_relative_path(info: ZipInfo) -> PurePosixPath:
    if "\x00" in info.filename:
        raise UnsafeArchiveError("压缩包包含 NUL 字符")
    raw = PurePosixPath(info.filename.replace("\\", "/"))
    if raw.is_absolute() or any(part == ".." for part in raw.parts):
        raise UnsafeArchiveError(f"检测到路径穿越：{info.filename}")
    if not raw.parts or raw.parts[0].endswith(":"):
        raise UnsafeArchiveError(f"检测到非法路径：{info.filename}")
    return raw


def _is_symlink(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _is_unsupported_file_type(info: ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode not in {0, 0o040000, 0o100000, 0o120000}


def _path_key(path: PurePosixPath) -> str:
    return unicodedata.normalize("NFC", str(path).rstrip("/")).casefold()


def _has_ignored_part(path: PurePosixPath) -> bool:
    return any(part in IGNORED_ARCHIVE_PARTS or part.startswith("._") for part in path.parts)


def inspect_zip(archive: Path, limits: ArchiveLimits) -> list[tuple[ZipInfo, PurePosixPath]]:
    checked: list[tuple[ZipInfo, PurePosixPath]] = []
    seen: dict[str, tuple[str, bool]] = {}
    required_directories: dict[str, str] = {}
    expanded = 0
    try:
        with ZipFile(archive) as bundle:
            for info in bundle.infolist():
                relative = _validated_relative_path(info)
                if _has_ignored_part(relative):
                    continue
                if _is_symlink(info):
                    raise UnsafeArchiveError(f"不允许符号链接：{info.filename}")
                if _is_unsupported_file_type(info):
                    raise UnsafeArchiveError(f"不允许特殊文件：{info.filename}")

                normalized = str(relative).rstrip("/")
                key = _path_key(relative)
                if existing := seen.get(key):
                    raise UnsafeArchiveError(
                        f"压缩包包含冲突路径：{existing[0]} 与 {normalized}"
                    )
                parts = key.split("/")
                for index in range(1, len(parts)):
                    parent = "/".join(parts[:index])
                    if parent in seen and not seen[parent][1]:
                        raise UnsafeArchiveError(
                            f"压缩包包含文件与目录冲突：{seen[parent][0]} 与 {normalized}"
                        )
                    required_directories.setdefault(parent, normalized)
                if not info.is_dir() and (descendant := required_directories.get(key)):
                    raise UnsafeArchiveError(
                        f"压缩包包含文件与目录冲突：{normalized} 与 {descendant}"
                    )

                seen[key] = (normalized, info.is_dir())
                if len(checked) >= limits.max_files:
                    raise UnsafeArchiveError("压缩包文件数超过限制")
                expanded += info.file_size
                if expanded > limits.max_expanded_bytes:
                    raise UnsafeArchiveError("压缩包展开大小超过限制")
                checked.append((info, relative))
    except (BadZipFile, LargeZipFile, NotImplementedError, RuntimeError) as exc:
        raise UnsafeArchiveError("ZIP 文件损坏、加密或格式不受支持") from exc
    return checked


def safe_extract_zip(archive: Path, destination: Path, limits: ArchiveLimits) -> None:
    checked = inspect_zip(archive, limits)
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with ZipFile(archive) as bundle:
            for info, relative in checked:
                target = (destination / Path(*relative.parts)).resolve()
                if root != target and root not in target.parents:
                    raise UnsafeArchiveError(f"解压目标越界：{info.filename}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
    except (BadZipFile, LargeZipFile, NotImplementedError, RuntimeError) as exc:
        raise UnsafeArchiveError("ZIP 文件损坏、加密或格式不受支持") from exc


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)((?:api[_-]?key|password|passwd|token|secret)\s*[:=]\s*)([^\s,;]+)"),
]


def redact_secrets(text: str, explicit_values: tuple[str, ...] = ()) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(r"\1[REDACTED]", result)
    for value in explicit_values:
        if value:
            result = result.replace(value, "[REDACTED]")
    return result


RISK_WORDS = re.compile(
    r"删除|清空|发布|授权|邀请|发送|支付|付款|购买|订阅|上传|下载|delete|remove|publish|"
    r"authorize|invite|send|pay|purchase|subscribe|upload|download",
    re.IGNORECASE,
)


def risky_action(action: dict) -> str | None:
    material = " ".join(
        str(value) for value in [action.get("reason"), action.get("instruction"), action.get("target")]
    )
    if action.get("action") == "request_approval":
        return action.get("reason") or "模型请求人工审批"
    if RISK_WORDS.search(material):
        return "动作可能产生删除、发布、外发、付费或文件传输等副作用"
    return None
