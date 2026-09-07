"""Configure the explicitly approved synthetic bank acceptance run through public APIs."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("task_id")
    parser.add_argument("fixtures", type=Path)
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        base = f"/api/tasks/{args.task_id}"

        def call(method, path, **kwargs):
            response = client.request(method, base + path, **kwargs)
            response.raise_for_status()
            return response.json()

        state = call("GET", "/runtime-plan")
        service = state["plan"]["services"][0]
        service["environment"] = {"GOPROXY": "https://goproxy.cn,direct", "SECRET_DIR": "/run/test-private", "userId": "synthetic", "DATA_DIR": "/tmp/bank-data"}
        service["install"] = [["go", "mod", "download"]]
        call("PUT", "/runtime-plan", json={"revision": state["revision"], "value": state["plan"]})
        file_ids = []
        for name, purpose in [("medical.xlsx", "Synthetic medical input, three fictional records"), ("bank.xlsx.enc", "Synthetic encrypted bank input, three fictional matching records")]:
            state = call("GET", "/runtime-plan")
            sha = hashlib.sha256((args.fixtures / name).read_bytes()).hexdigest()
            existing = next((item for item in state["files"] if item["name"] == name and item["sha256"] == sha), None)
            if existing:
                file_ids.append(existing["id"])
                continue
            with (args.fixtures / name).open("rb") as stream:
                file = call("POST", "/test-files", data={"purpose": purpose, "revision": state["revision"]}, files={"file": (name, stream)})
            file_ids.append(file["id"])
        task = call("GET", "")
        features = sorted([f for f in task["features"] if f["selected"]], key=lambda f: f["position"])
        assert len(features) == 4, "This script only applies to the four-feature bank task"
        # Match the existing feature identities, never replace historical features.
        medical = next(f for f in features if "医保" in f["title"])
        bank = next(f for f in features if "银行" in f["title"])
        compute = next(f for f in features if "计算" in f["title"])
        download = next(f for f in features if "下载" in f["title"])
        ordered = [medical, bank, compute, download]
        goals = ["使用已确认的医保测试文件上传，验证接口成功，不要再次上传。", "使用已确认的银行加密文件上传，沿用当前医保上传产生的任务编号，不要重新上传医保。", "点击执行计算，验证计算接口成功，不要重新上传文件。", "点击下载结果，使用 download 动作捕获实际 Excel 文件，不要重新上传或计算。"]
        for feature, goal in zip(ordered, goals, strict=True):
            feature["goal"] = goal
        state = call("GET", "/runtime-plan")
        call("PUT", f"/features?revision={state['revision']}", json=features)
        rules = {}
        for index, feature in enumerate(ordered):
            rule = {"depends_on": [ordered[index - 1]["id"]] if index else [], "file_ids": [file_ids[index]] if index < 2 else []}
            if index < 3:
                rule["success_response"] = {"path": ["/api/medical/upload", "/api/bank/upload", "/api/compute"][index], "json_equals": {"ok": True}, "capture_fields": ["jobId", "rowsCount", "resultRows"]}
            else:
                rule["download_extension"] = ".xlsx"
            rules[feature["id"]] = rule
        state = call("GET", "/runtime-plan")
        state = call("PUT", "/execution-config", json={"revision": state["revision"], "value": {"features": rules}})
        for stage in ("runtime-plan", "execution-config"):
            call("POST", f"/{stage}/confirm", json={"revision": state["revision"]})
        print(call("POST", "/run"))


if __name__ == "__main__":
    main()
