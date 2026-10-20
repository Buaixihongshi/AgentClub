#!/usr/bin/env python3
"""Build a pinned LiveCodeBench subset without running remote dataset scripts or models."""
import argparse
import base64
import hashlib
import io
import json
import pickle
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SELECTION = ROOT / "eval/pilots/open-code-v1/selection.json"


class DataOnlyUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError("Dataset pickle must not reference Python globals")

    def persistent_load(self, pid):
        raise ValueError("Dataset pickle must not use persistent references")


def decode_private(encoded):
    try:
        result = json.loads(encoded)
    except json.JSONDecodeError:
        compressed = base64.b64decode(encoded, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, 64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024 or not decoder.eof:
            raise ValueError("Private test payload exceeds the bounded decoder size")
        result = json.loads(DataOnlyUnpickler(io.BytesIO(raw)).load())
    if not isinstance(result, list) or not result:
        raise ValueError("Missing private tests")
    for case in result:
        if case.get("testtype") != "stdin" or not isinstance(case.get("input"), str) or not isinstance(case.get("output"), str):
            raise ValueError("Only text stdin/stdout cases are supported")
    return result


def prepare(source, output):
    source, output = Path(source), Path(output)
    selection = json.loads(SELECTION.read_text())
    with source.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if source.stat().st_size != selection["source"]["bytes"] or digest != selection["source"]["sha256"]:
        raise ValueError("Source does not match pinned official size and SHA256")
    if (output / "manifest.json").exists():
        raise ValueError("Use a fresh output directory")
    wanted = {(row["platform"], row["question_id"]): row for row in selection["tasks"]}
    selected = {}
    with source.open() as handle:
        for line in handle:
            row = json.loads(line)
            key = row["platform"], row["question_id"]
            if key in wanted:
                if key in selected:
                    raise ValueError("Duplicate source task")
                selected[key] = row
    if set(selected) != set(wanted):
        raise ValueError("Selected IDs missing from pinned source")
    public_dir, private_dir = output / "public", output / "private"
    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(exist_ok=True)
    manifest = {"id": selection["id"], "model_calls": 0, "source_sha256": digest,
                "selection_sha256": hashlib.sha256(SELECTION.read_bytes()).hexdigest(),
                "source": selection["source"], "tasks": [], "files": {}}
    for key, spec in wanted.items():
        row = selected[key]
        examples, hidden = json.loads(row["public_test_cases"]), decode_private(row["private_test_cases"])
        if any(test.get("testtype") != "stdin" for test in examples):
            raise ValueError("Selected task has a non-stdin example")
        question_id = row["question_id"]
        # Analysis tags can reveal the intended algorithm. Keep them exclusively
        # in the host manifest, never in the model-facing original problem.
        public = {"platform": row["platform"], "question_id": question_id,
                  "title": row["question_title"], "difficulty": row["difficulty"],
                  "contest_id": row["contest_id"], "contest_date": row["contest_date"],
                  "source_url": f"https://atcoder.jp/contests/{row['contest_id']}/tasks/{question_id}",
                  "statement": row["question_content"], "starter_code": row["starter_code"],
                  "public_test_cases": examples,
                  "submission_instruction": "Return one complete Python 3.12 program reading stdin and writing stdout. No Markdown or explanation. All groups receive these same examples and statement."}
        private = {"question_id": question_id, "public_test_cases": examples, "private_test_cases": hidden}
        for path, data in ((public_dir / f"{question_id}.json", public), (private_dir / f"{question_id}.json", private)):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            manifest["files"][str(path.relative_to(output))] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["tasks"].append({**spec, "title": row["question_title"], "difficulty": row["difficulty"],
                                  "public_cases": len(examples), "private_cases": len(hidden)})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "output/eval/source-cache/lcb-test6.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "output/eval/open-code-v1")
    args = parser.parse_args()
    result = prepare(args.source, args.output)
    print(json.dumps({"tasks": result["tasks"], "source_sha256": result["source_sha256"], "model_calls": 0}, ensure_ascii=False, indent=2))
