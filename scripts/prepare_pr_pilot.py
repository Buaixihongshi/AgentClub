#!/usr/bin/env python3
"""Prepare own-PR repository tasks and optionally qualify base -> reference behavior.

No model calls, no changes to the working checkout, no PR publication or merge.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "eval/pilots/pr-replay-v1"

CANDIDATES = {
    1: ("needs_split", "A2A 空闲与失败处理；与后续同根因修复保持同一家族"),
    2: ("defer_infrastructure", "CI 质量门禁，不作首批业务修复题"),
    3: ("needs_split", "仓库维护基础功能范围较大，需抽取独立验收目标"),
    4: ("defer_prompt_behavior", "主要涉及提示策略，fake 通过不能证明真实意图遵循"),
    5: ("defer_persona", "人格和体验变化需另设验收，不能仅用关键词测试"),
    6: ("exclude_docs_only", "规划文档，没有代码修复验收"),
    7: ("selected_development", "先回放旧插话在后续任务上下文中的可见性"),
    8: ("needs_split", "圆桌预算和控制可验收，但整 PR 太大"),
    9: ("candidate_frontend", "参与者选择、停止与继续可做功能题，需前端验收环境"),
    10: ("candidate_frontend", "交互追踪界面可做行为题，不能只检查 CSS"),
    11: ("exclude_docs_only", "协作和审批文档，不作代码修复题"),
    12: ("defer_infrastructure", "环境与发布门禁，另归运维配置任务"),
    13: ("selected_development", "先回放同会话跨任务文件读取，不混入整 PR 所有修复"),
    14: ("defer_visual", "主要为视觉样式，需要明确视觉验收而非 pytest 替代"),
    15: ("candidate_cross_stack", "超时失败记录和活动展示贴近业务，需拆分并验证前后端"),
    16: ("needs_split", "进程复用、凭证与恢复状态机需故障注入，先控制任务范围"),
    17: ("exclude_open", "尚未合并，不作为已接受的参考实现"),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(args, *, cwd=ROOT, binary=False):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, *args], cwd=cwd,
                            env=env, capture_output=True, text=not binary, check=True, timeout=120)
    return result.stdout


def snapshot(commit, target):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=False)
    archive = git(["archive", "--format=tar", commit], binary=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        handle.extractall(target, filter="data")
    # A snapshot nested under the project must not inherit the parent's .git.
    # Start fresh history with exactly one commit and no source remotes.
    git(["init", "-q", "--initial-branch=benchmark-base"], cwd=target)
    git(["config", "core.hooksPath", os.devnull], cwd=target)
    git(["add", "--all"], cwd=target)
    git(["-c", "user.name=Benchmark", "-c", "user.email=benchmark@localhost",
         "commit", "-q", "-m", "Benchmark initial snapshot"], cwd=target)


def read_junit(path):
    root = ET.parse(path).getroot()
    result = {}
    for case in root.iter("testcase"):
        name = case.attrib["name"]
        if name in result:
            raise ValueError("Ambiguous duplicate test name in JUnit")
        if case.find("error") is not None:
            status = "error"
        elif case.find("failure") is not None:
            status = "failed"
        elif case.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        result[name] = status
    return result


def qualification_passes(task, base, reference):
    expected = set(task["fail_to_pass"] + task["pass_to_pass"])
    return (bool(task["fail_to_pass"]) and bool(task["pass_to_pass"])
            and base["returncode"] in (0, 1) and reference["returncode"] == 0
            and set(base["checks"]) == expected == set(reference["checks"])
            and all(base["checks"][name] == "failed" for name in task["fail_to_pass"])
            and all(base["checks"][name] == "passed" for name in task["pass_to_pass"])
            and all(value == "passed" for value in reference["checks"].values()))


def phase(workspace, task, log_dir, label):
    report = log_dir / f"{label}.xml"
    nodes = [f"backend/tests/test_pr_replay_contract.py::{name}"
             for name in task["fail_to_pass"] + task["pass_to_pass"]]
    command = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--confcutdir", str(workspace),
               "--junitxml", str(report), *nodes]
    env = {**os.environ, "PYTHONPATH": str(workspace), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    try:
        completed = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=60)
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, returncode = "", "qualification timeout", 124
    (log_dir / f"{label}.stdout.txt").write_text(stdout)
    (log_dir / f"{label}.stderr.txt").write_text(stderr)
    checks = read_junit(report) if report.exists() else {}
    return {"returncode": returncode, "checks": checks, "report": str(report), "command": command}


def qualify(task, private_dir):
    workspace = private_dir / "replay"
    snapshot(task["base_commit"], workspace)
    shutil.copyfile(private_dir / "grading.py", workspace / "backend/tests/test_pr_replay_contract.py")
    base = phase(workspace, task, private_dir, "base")
    patch = private_dir / "reference.patch"
    git(["apply", "--check", str(patch)], cwd=workspace)
    git(["apply", str(patch)], cwd=workspace)
    reference = phase(workspace, task, private_dir, "reference")
    return {"qualified": qualification_passes(task, base, reference), "base": base, "reference": reference,
            "scope": "Offline test qualification of the selected subtask, not a model repair result or proof that all requirements are exhaustively tested"}


def prepare(prs_path, output, *, run_qualification=False):
    output = Path(output).resolve()
    if (output / "manifest.json").exists():
        raise ValueError("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    prs = json.loads(Path(prs_path).read_text())
    by_number = {row["number"]: row for row in prs}
    selection = json.loads((PACK / "tasks.json").read_text())
    catalog = []
    for pr in prs:
        status, reason = CANDIDATES.get(pr["number"], ("not_reviewed", "尚未评估"))
        if pr["state"] != "MERGED":
            status, reason = "exclude_not_merged", "尚无已合并参考版本"
        catalog.append({key: pr[key] for key in ("number", "title", "state", "url", "changedFiles", "additions", "deletions", "mergedAt")}
                       | {"disposition": status, "reason": reason})
    manifest = {"id": selection["id"], "created_at": datetime.now(timezone.utc).isoformat(), "model_calls": 0,
                "source_prs_sha256": digest(prs_path), "selection_sha256": digest(PACK / "tasks.json"),
                "pr_count": len(prs), "merged_prs": sum(row["state"] == "MERGED" for row in prs),
                "locked_tasks": 0, "tasks": []}
    (output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    distributions = sorted({(dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions() if dist.metadata["Name"]})
    environment = {"python": sys.version, "platform": platform.platform(), "packages": distributions,
                   "scope": "Observed host venv for offline qualification; not a pinned container image"}
    (output / "environment.json").write_text(json.dumps(environment, ensure_ascii=False, indent=2) + "\n")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for task in selection["tasks"]:
        pr = by_number[task["source_pr"]]
        if pr["state"] != "MERGED" or pr["mergeCommit"]["oid"] != task["fix_commit"]:
            raise ValueError("Selected PR is not the recorded merged implementation")
        parent = git(["rev-parse", task["fix_commit"] + "^1"]).strip()
        if parent != task["base_commit"]:
            raise ValueError("Base must be the recorded merge/squash first parent")
        public, private = output / "public" / task["id"], output / "private" / task["id"]
        public.mkdir(parents=True)
        private.mkdir(parents=True)
        snapshot(task["base_commit"], public / "repo")
        public_task = {"id": task["id"], "problem_statement": task["problem_statement"],
                       "deliverable": "Modify this repository to satisfy the stated behavior; submit one final patch. No PR creation, publication, merge, deployment or model fallback is required."}
        (public / "task.json").write_text(json.dumps(public_task, ensure_ascii=False, indent=2) + "\n")
        patch = git(["diff", "--binary", task["base_commit"], task["fix_commit"], "--", *task["reference_paths"]], binary=True)
        (private / "reference.patch").write_bytes(patch)
        shutil.copyfile(PACK / "checks" / task["check_file"], private / "grading.py")
        record = {**task, "base_tree": git(["rev-parse", task["base_commit"] + "^{tree}"]).strip(),
                  "reference_patch_sha256": digest(private / "reference.patch"), "grading_sha256": digest(private / "grading.py"),
                  "public_task_sha256": digest(public / "task.json"), "public_git_commit_count": int(git(["rev-list", "--count", "HEAD"], cwd=public / "repo")),
                  "public_remotes": git(["remote"], cwd=public / "repo").strip()}
        if run_qualification:
            print(f"[{task['id']}] qualifying original base and PR reference", flush=True)
            record["qualification"] = qualify(task, private)
            print(f"  qualified={record['qualification']['qualified']}", flush=True)
        manifest["tasks"].append(record)
        (private / "provenance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    manifest["qualified_tasks"] = sum(task.get("qualification", {}).get("qualified", False) for task in manifest["tasks"])
    manifest["status"] = ("qualified_development_pack" if manifest["qualified_tasks"] == len(selection["tasks"])
                          else "qualification_failed" if run_qualification else "prepared_not_qualified")
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prs", type=Path, default=ROOT / "output/eval/pr-corpus-v1/prs.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/eval/pr-replay-v1")
    parser.add_argument("--qualify", action="store_true")
    args = parser.parse_args()
    result = prepare(args.prs, args.output, run_qualification=args.qualify)
    print(json.dumps({key: result[key] for key in ("status", "pr_count", "merged_prs", "qualified_tasks", "locked_tasks", "model_calls")}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["status"] == "qualification_failed" else 0)
