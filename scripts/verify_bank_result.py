"""Verify the captured bank download and response correlation, without exposing file data to an LLM."""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx


def rows(content):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        assert bundle.testzip() is None
        strings = ["".join(node.itertext()) for node in ET.fromstring(bundle.read("xl/sharedStrings.xml")).findall("s:si", ns)] if "xl/sharedStrings.xml" in bundle.namelist() else []
        result = []
        for row in ET.fromstring(bundle.read("xl/worksheets/sheet1.xml")).findall("s:sheetData/s:row", ns):
            values = []
            for cell in row.findall("s:c", ns):
                value = cell.findtext("s:v", default="", namespaces=ns)
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.find("s:is", ns).itertext())
                values.append(value)
            result.append(values)
        return result


def main():
    base_url, task_id, expected_path, output_path = sys.argv[1:]
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=base_url, timeout=60) as client:
        task = client.get(f"/api/tasks/{task_id}").json()
        state = client.get(f"/api/tasks/{task_id}/runtime-plan").json()
        run = state["runs"][0]
        assert run["status"] == "completed", run
        features = run["data"]["features"]
        assert len(features) == 4 and all(f["status"] == "completed" for f in features.values())
        responses = [r for f in features.values() for r in f.get("responses", [])]
        jobs = {r["observed"]["jobId"] for r in responses}
        assert len(responses) == 3 and len(jobs) == 1, responses
        job = jobs.pop()
        artifact = next(a for a in task["artifacts"] if a["name"].endswith(job + ".xlsx"))
        response = client.get(artifact["url"])
        response.raise_for_status()
        actual = rows(response.content)
        expected = [[str(v) for v in row] for row in json.loads(Path(expected_path).read_text())]
        assert actual == expected, {"actual": actual, "expected": expected}
        (output / "result.xlsx").write_bytes(response.content)
        verification = {"task": task_id, "run": run["run_id"], "jobId": job, "responses": responses, "rows": actual, "verified": True}
        (output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2))
        print(json.dumps(verification, ensure_ascii=False))


if __name__ == "__main__":
    main()
