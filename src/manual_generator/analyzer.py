from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(slots=True)
class DetectedPlan:
    project_type: str
    working_directory: str
    install_command: list[str]
    start_command: list[str]
    environment: dict[str, str]
    detected_files: list[str]
    start_url: str


@dataclass(slots=True)
class DetectedFeature:
    title: str
    entry_path: str
    goal: str


@dataclass(slots=True)
class FeatureEvidence:
    id: str
    kind: str
    label: str
    entry_path: str
    source: str
    line: int
    details: dict[str, str]


IGNORE_PARTS = {"node_modules", ".git", ".venv", "dist", "build", "vendor"}
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".html"}
ROUTE_PATTERNS = [
    re.compile(r"(?:path|href|to)\s*[=:]\s*[\"'](/[^\"'?#]*)[\"']"),
    re.compile(r"@(?:app|router)\.(?:get|post|put|delete|patch)\([\"'](/[^\"']*)[\"']"),
]
HEADING_PATTERN = re.compile(r"<h[1-6][^>]*>\s*([^<{][^<]*)\s*</h[1-6]>", re.IGNORECASE)
BUTTON_PATTERN = re.compile(r"<button\b(?P<attrs>[^>]*)>(?P<body>.*?)</button>", re.IGNORECASE | re.DOTALL)
FILE_INPUT_PATTERN = re.compile(r"<input\b(?P<attrs>[^>]*\btype\s*=\s*[\"']file[\"'][^>]*)/?>", re.IGNORECASE | re.DOTALL)
API_CALL_PATTERN = re.compile(r"\b(?P<client>[A-Za-z_$][\w$]*)\.(?P<method>get|post|put|patch|delete)\(\s*[\"'`](?P<path>/[^\"'`]*)[\"'`]", re.IGNORECASE)
FUNCTION_PATTERN = re.compile(r"(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")
STRING_PATTERN = re.compile(r"[\"'`]([^\"'`{}\n]{1,120})[\"'`]")


def _project_roots(workspace: Path) -> list[Path]:
    children = [p for p in workspace.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return [children[0], workspace]
    return [workspace]


def detect_launch_plan(workspace: Path) -> DetectedPlan:
    for root in _project_roots(workspace):
        rel = str(root.relative_to(workspace)) or "."
        compose = next((name for name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml") if (root / name).exists()), None)
        if compose:
            return DetectedPlan("compose", rel, [], ["docker", "compose", "-f", compose, "up", "--build"], {}, [compose], "http://127.0.0.1:3000")
        if (root / "Dockerfile").exists():
            return DetectedPlan("docker", rel, ["docker", "build", "-t", "manual-generator-target", "."], ["docker", "run", "--rm", "-p", "3000:3000", "manual-generator-target"], {}, ["Dockerfile"], "http://127.0.0.1:3000")
        if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
            install = ["python", "-m", "pip", "install", "-e", "."] if (root / "pyproject.toml").exists() else ["python", "-m", "pip", "install", "-r", "requirements.txt"]
            start = _python_start_command(root)
            files = [p.name for p in (root / "pyproject.toml", root / "requirements.txt") if p.exists()]
            return DetectedPlan("python", rel, install, start, {"PORT": "8001"}, files, "http://127.0.0.1:8001")
        if (root / "package.json").exists():
            package = json.loads((root / "package.json").read_text(errors="ignore"))
            scripts = package.get("scripts", {})
            script = "dev" if "dev" in scripts else "start" if "start" in scripts else "preview"
            manager = "npm"
            install = [manager, "ci"] if (root / "package-lock.json").exists() else [manager, "install"]
            script_value = str(scripts.get(script, ""))
            if "vite" in script_value:
                start = [manager, "run", script, "--", "--host", "127.0.0.1"]
                start_url = "http://127.0.0.1:5173"
            elif "next" in script_value:
                start = [manager, "run", script, "--", "-H", "127.0.0.1"]
                start_url = "http://127.0.0.1:3000"
            else:
                start = [manager, "run", script]
                start_url = "http://127.0.0.1:3000"
            return DetectedPlan("node", rel, install, start, {}, ["package.json"], start_url)
    return DetectedPlan("custom", ".", [], ["python", "-m", "http.server", "8001"], {}, [], "http://127.0.0.1:8001")


def _python_start_command(root: Path) -> list[str]:
    candidates = [("main.py", "main:app"), ("app.py", "app:app"), ("server.py", "server:app")]
    for filename, target in candidates:
        if (root / filename).exists():
            text = (root / filename).read_text(errors="ignore")
            if "FastAPI(" in text:
                return ["python", "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", "8001"]
            if "Flask(" in text:
                return ["python", filename]
    if (root / "manage.py").exists():
        return ["python", "manage.py", "runserver", "127.0.0.1:8001"]
    return ["python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"]


def collect_feature_evidence(workspace: Path) -> list[FeatureEvidence]:
    evidence: list[FeatureEvidence] = []
    scanned = 0
    for path in workspace.rglob("*"):
        if scanned >= 400 or not path.is_file() or any(part in IGNORE_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES or path.stat().st_size > 512_000:
            continue
        scanned += 1
        text = path.read_text(errors="ignore")
        source = str(path.relative_to(workspace))
        headings = [(match.start(), _plain_text(match.group(1))) for match in HEADING_PATTERN.finditer(text)]

        for pattern in ROUTE_PATTERNS:
            for match in pattern.finditer(text):
                route = match.group(1) or "/"
                if _valid_entry_path(route) and not route.startswith("/api/"):
                    _append_evidence(evidence, "route", _route_title(route), route, source, text, match.start(), {})

        for match in FILE_INPUT_PATTERN.finditer(text):
            attrs = match.group("attrs")
            label = _nearest_heading(headings, match.start()) or _attribute(attrs, "aria-label") or "文件上传"
            details = {"accept": _attribute(attrs, "accept"), "handler": _handler_name(attrs)}
            _append_evidence(evidence, "file_input", label, "/", source, text, match.start(), details)

        for match in BUTTON_PATTERN.finditer(text):
            attrs, body = match.group("attrs"), match.group("body")
            section = _nearest_heading(headings, match.start())
            label = _button_label(body) or _attribute(attrs, "aria-label") or section
            if section and (
                any(token in label for token in ("}", ">", "()"))
                or label.endswith("...")
            ):
                label = section
            if label:
                details = {"handler": _handler_name(attrs), "section": section}
                _append_evidence(evidence, "button", label, "/", source, text, match.start(), details)

        functions = [(match.start(), match.group("name")) for match in FUNCTION_PATTERN.finditer(text)]
        for match in API_CALL_PATTERN.finditer(text):
            api_path = match.group("path")
            details = {
                "method": match.group("method").upper(),
                "path": api_path,
                "handler": _nearest_function(functions, match.start()),
            }
            _append_evidence(evidence, "api_call", api_path, "/", source, text, match.start(), details)
    return evidence[:200]


def detect_features(workspace: Path) -> list[DetectedFeature]:
    return static_features(collect_feature_evidence(workspace))


def static_features(evidence: list[FeatureEvidence]) -> list[DetectedFeature]:
    interactions = [item for item in evidence if item.kind in {"file_input", "button"}]
    features: list[DetectedFeature] = []
    for item in interactions:
        if item.kind == "file_input":
            title = _upload_title(item.label)
            subject = _clean_label(item.label).replace("上传", "").strip()
            goal = f"选择并上传{subject}所需文件，确认上传结果"
        else:
            title = _clean_label(item.label)
            if not title or _generic_button(title):
                continue
            goal = f"执行{title}并确认操作结果"
            lowered = title.lower()
            if "计算" in title or "compute" in lowered:
                goal += "；前置条件：完成所需文件上传"
            if "下载" in title or "导出" in title or "download" in lowered:
                goal += "；前置条件：已生成可下载结果"
        features.append(DetectedFeature(title, item.entry_path, goal))

    features = _deduplicate_features(features)
    if features:
        route_features = [
            DetectedFeature(item.label or _route_title(item.entry_path), item.entry_path, f"打开并说明{item.label or _route_title(item.entry_path)}的主要功能")
            for item in _unique_routes([item for item in evidence if item.kind == "route"])
            if item.entry_path != "/"
        ]
        return _deduplicate_features(features + route_features)[:50]

    routes = [item for item in evidence if item.kind == "route"]
    if not any(item.entry_path == "/" for item in routes):
        routes.insert(0, FeatureEvidence("default-home", "route", "首页", "/", "", 1, {}))
    return [
        DetectedFeature(item.label or _route_title(item.entry_path), item.entry_path, f"打开并说明{item.label or _route_title(item.entry_path)}的主要功能")
        for item in _unique_routes(routes)[:50]
    ]


def validate_model_features(items: list[dict], evidence: list[FeatureEvidence]) -> list[DetectedFeature]:
    evidence_ids = {item.id for item in evidence}
    validated: list[DetectedFeature] = []
    for item in items[:50]:
        title = str(item.get("title", "")).strip()
        entry_path = str(item.get("entry_path", "")).strip()
        goal = str(item.get("goal", "")).strip()
        references = item.get("evidence_ids")
        if (
            not title
            or len(title) > 240
            or not goal
            or not _valid_entry_path(entry_path)
            or not isinstance(references, list)
            or not references
            or any(reference not in evidence_ids for reference in references)
        ):
            continue
        validated.append(DetectedFeature(title, entry_path, goal))
    return _deduplicate_features(validated)[:50]


def model_inventory_complete(
    items: list[dict],
    evidence: list[FeatureEvidence],
    features: list[DetectedFeature],
    fallback: list[DetectedFeature],
) -> bool:
    required_ids = {
        item.id
        for item in evidence
        if item.kind == "file_input"
        or (
            item.kind == "button"
            and _clean_label(item.label)
            and not _generic_button(_clean_label(item.label))
        )
    }
    referenced_ids = {
        reference
        for item in items
        if isinstance(item.get("evidence_ids"), list)
        for reference in item["evidence_ids"]
    }
    return bool(features) and len(features) >= len(fallback) and required_ids <= referenced_ids


def evidence_payload(evidence: list[FeatureEvidence]) -> list[dict]:
    return [asdict(item) for item in evidence]


def _append_evidence(
    evidence: list[FeatureEvidence],
    kind: str,
    label: str,
    entry_path: str,
    source: str,
    text: str,
    offset: int,
    details: dict[str, str],
) -> None:
    clean_details = {key: value for key, value in details.items() if value}
    identity = (kind, label.strip(), entry_path, source, text.count("\n", 0, offset) + 1, tuple(clean_details.items()))
    if any((item.kind, item.label, item.entry_path, item.source, item.line, tuple(item.details.items())) == identity for item in evidence):
        return
    evidence.append(FeatureEvidence(f"E{len(evidence) + 1:03d}", kind, label.strip(), entry_path, source, identity[4], clean_details))


def _attribute(attrs: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*[\"']([^\"']*)[\"']", attrs, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _handler_name(attrs: str) -> str:
    match = re.search(r"\bon(?:Click|Change|Submit)\s*=\s*\{\s*(?:\([^)]*\)\s*=>\s*)?([A-Za-z_$][\w$]*)", attrs)
    return match.group(1) if match else ""


def _nearest_heading(headings: list[tuple[int, str]], offset: int) -> str:
    return next((label for position, label in reversed(headings) if position < offset and label), "")


def _nearest_function(functions: list[tuple[int, str]], offset: int) -> str:
    return next((name for position, name in reversed(functions) if position < offset), "")


def _button_label(body: str) -> str:
    plain = _plain_text(re.sub(r"\{[^{}]*\}", " ", body))
    strings = [value.strip() for value in STRING_PATTERN.findall(body) if value.strip()]
    candidates = strings or ([plain.rsplit(">", 1)[-1].strip()] if plain else [])
    return max(candidates, key=len, default="")


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def _clean_label(value: str) -> str:
    value = re.sub(r"\s*[（(][^）)]*[）)]\s*", "", value).strip()
    return re.sub(r"\s+", " ", value)


def _upload_title(label: str) -> str:
    detail = " ".join(re.findall(r"[（(]([^）)]*)[）)]", label)).strip()
    clean = _clean_label(label).replace("上传", "").strip()
    return f"上传{clean}{(' ' + detail) if detail else ''}".strip()


def _generic_button(label: str) -> bool:
    return label.lower() in {"确定", "确认", "取消", "关闭", "提交", "保存", "ok", "cancel", "submit", "save"}


def _deduplicate_features(features: list[DetectedFeature]) -> list[DetectedFeature]:
    seen: set[tuple[str, str]] = set()
    result: list[DetectedFeature] = []
    for feature in features:
        key = (re.sub(r"\s+", "", feature.title).lower(), feature.entry_path)
        if key not in seen:
            seen.add(key)
            result.append(feature)
    return result


def _unique_routes(routes: list[FeatureEvidence]) -> list[FeatureEvidence]:
    seen: set[str] = set()
    result: list[FeatureEvidence] = []
    for item in routes:
        if item.entry_path not in seen:
            seen.add(item.entry_path)
            result.append(item)
    return result


def _valid_entry_path(route: str) -> bool:
    if not route or not route.startswith("/") or any(token in route for token in (":", "*", "?", "#")):
        return False
    parsed = urlsplit(route)
    return not parsed.scheme and not parsed.netloc and parsed.path == route


def _route_title(route: str) -> str:
    if route == "/":
        return "首页"
    tail = route.rstrip("/").split("/")[-1]
    return tail.replace("-", " ").replace("_", " ").strip().title() or "功能页面"
