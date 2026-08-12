import json
from pathlib import Path

from manual_generator.analyzer import (
    collect_feature_evidence,
    detect_features,
    detect_launch_plan,
    model_inventory_complete,
    validate_model_features,
)


def test_detects_node_project_and_routes(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
    (tmp_path / "app.tsx").write_text('<Route path="/users" /><a href="/settings">设置</a>')
    plan = detect_launch_plan(tmp_path)
    features = detect_features(tmp_path)
    assert plan.project_type == "node"
    assert plan.start_command[:3] == ["npm", "run", "dev"]
    assert {item.entry_path for item in features} >= {"/", "/users", "/settings"}


def test_prefers_compose(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services: {}")
    (tmp_path / "package.json").write_text("{}")
    assert detect_launch_plan(tmp_path).project_type == "compose"


def test_detects_fastapi(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
    plan = detect_launch_plan(tmp_path)
    assert plan.project_type == "python"
    assert "uvicorn" in plan.start_command


def test_detects_next_start_command(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"dev": "next dev"}})
    )
    plan = detect_launch_plan(tmp_path)
    assert plan.start_command[-2:] == ["-H", "127.0.0.1"]
    assert plan.start_url.endswith(":3000")


def test_scans_mjs_routes(tmp_path: Path) -> None:
    (tmp_path / "server.mjs").write_text('<a href="/users">用户管理</a>')
    assert "/users" in {item.entry_path for item in detect_features(tmp_path)}


def test_detects_single_page_business_actions(tmp_path: Path) -> None:
    (tmp_path / "App.tsx").write_text(
        """
        <h3>医保上传 (明文 Excel)</h3>
        <input type="file" accept=".xlsx" />
        <h3>银行上传 (加密文件)</h3>
        <input type="file" accept=".enc" />
        <h3>计算</h3><button onClick={() => doCompute()}>{computing ? "计算中..." : "计算得分"}</button>
        <h3>下载结果</h3><button onClick={() => downloadResult()}>下载结果 Excel</button>
        """
    )

    features = detect_features(tmp_path)

    assert [item.title for item in features] == [
        "上传医保 明文 Excel",
        "上传银行 加密文件",
        "计算",
        "下载结果 Excel",
    ]
    assert "前置条件" in features[2].goal
    assert "前置条件" in features[3].goal
    assert all(item.entry_path == "/" for item in features)


def test_validates_model_features_against_evidence(tmp_path: Path) -> None:
    (tmp_path / "App.tsx").write_text('<h3>计算</h3><button>计算得分</button>')
    evidence = collect_feature_evidence(tmp_path)
    evidence_id = next(item.id for item in evidence if item.kind == "button")

    features = validate_model_features(
        [
            {"title": "计算得分", "entry_path": "/", "goal": "完成计算", "evidence_ids": [evidence_id]},
            {"title": "虚构功能", "entry_path": "/", "goal": "不存在", "evidence_ids": ["missing"]},
            {"title": "站外功能", "entry_path": "https://example.com", "goal": "非法", "evidence_ids": [evidence_id]},
            {"title": "计算得分", "entry_path": "/", "goal": "重复", "evidence_ids": [evidence_id]},
        ],
        evidence,
    )

    assert len(features) == 1
    assert features[0].title == "计算得分"


def test_rejects_incomplete_model_inventory(tmp_path: Path) -> None:
    (tmp_path / "App.tsx").write_text(
        '<h3>上传文件</h3><input type="file" /><h3>计算</h3><button>计算</button>'
    )
    evidence = collect_feature_evidence(tmp_path)
    fallback = detect_features(tmp_path)
    input_id = next(item.id for item in evidence if item.kind == "file_input")
    proposals = [
        {
            "title": "上传文件",
            "entry_path": "/",
            "goal": "上传文件",
            "evidence_ids": [input_id],
        }
    ]
    features = validate_model_features(proposals, evidence)

    assert not model_inventory_complete(proposals, evidence, features, fallback)
