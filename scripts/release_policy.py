"""Read GitHub provenance and install the existing owner status as a real gate.

This module never dispatches the owner workflow or posts approval statuses.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess

from scripts.environment_common import EnvironmentError, ROOT, git

CI_NAMES = ("Backend tests", "Frontend tests and build")
OWNER_CONTEXT = "Owner approval"
ACTIONS_APP = 15368


def repo_name(root=ROOT):
    url = git(root, "remote", "get-url", "origin")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([^/\s]+/[^/\s]+?)(?:\.git)?/?", url)
    if not match: raise EnvironmentError("Release tools require the configured GitHub origin")
    return match.group(1)


class GitHub:
    def __init__(self, repo=None): self.repo = repo or repo_name()

    def get(self, suffix):
        result = subprocess.run(["gh", "api", f"repos/{self.repo}/{suffix}"], capture_output=True, text=True)
        if result.returncode: raise EnvironmentError(f"GitHub read failed for {suffix}: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def put(self, suffix, payload):
        result = subprocess.run(["gh", "api", "--method", "PUT", f"repos/{self.repo}/{suffix}", "--input", "-"],
                                input=json.dumps(payload), capture_output=True, text=True)
        if result.returncode: raise EnvironmentError(f"GitHub gate update failed: {result.stderr.strip()}")
        return json.loads(result.stdout)


def check_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha): raise EnvironmentError("A full lowercase commit SHA is required")
    return sha


def ci_result(api, sha):
    checks = api.get(f"commits/{check_sha(sha)}/check-runs?filter=latest&per_page=100")["check_runs"]
    latest = {}
    for check in sorted(checks, key=lambda c: c.get("started_at") or "", reverse=True):
        latest.setdefault(check["name"], check)
    return {name: bool(name in latest and latest[name].get("app", {}).get("id") == ACTIONS_APP
                       and latest[name]["status"] == "completed" and latest[name]["conclusion"] == "success")
            for name in CI_NAMES}


def owner_evidence(api, sha):
    statuses = api.get(f"commits/{check_sha(sha)}/status").get("statuses", [])
    matching = sorted((s for s in statuses if s["context"] == OWNER_CONTEXT),
                      key=lambda s: s.get("created_at", ""), reverse=True)
    if not matching or matching[0]["state"] != "success": raise EnvironmentError("Current head has no successful Owner approval")
    status = matching[0]
    match = re.fullmatch(r"https://github\.com/" + re.escape(api.repo) + r"/actions/runs/(\d+)", status.get("target_url") or "")
    if not match or status.get("creator", {}).get("login") != "github-actions[bot]":
        raise EnvironmentError("Owner approval must come from the trusted manual workflow")
    run = api.get(f"actions/runs/{match.group(1)}")
    if (run["event"] != "workflow_dispatch" or run["head_branch"] != "main" or
        run["actor"]["login"] != api.repo.split("/")[0] or run["conclusion"] != "success" or
        run["path"].split("@", 1)[0] != ".github/workflows/owner-release.yml"):
        raise EnvironmentError("Approval workflow provenance is invalid")
    return {"approved_head_sha": sha, "approval_run": status["target_url"], "approved_by": run["actor"]["login"]}


def prepare(api, number):
    pr = api.get(f"pulls/{int(number)}")
    ci = ci_result(api, pr["head"]["sha"])
    try: approval = owner_evidence(api, pr["head"]["sha"])
    except EnvironmentError: approval = None
    return {"pr": pr["number"], "head_sha": pr["head"]["sha"], "base": pr["base"]["ref"], "ci": ci,
            "ready_for_owner_review": pr["state"] == "open" and not pr["draft"] and pr["base"]["ref"] == "main" and all(ci.values()),
            "owner_approval": approval, "merged": bool(pr.get("merged"))}


def release_provenance(api, sha):
    check_sha(sha)
    main = api.get("git/ref/heads/main")["object"]["sha"]
    compare = api.get(f"compare/{sha}...{main}")
    if compare["status"] not in {"ahead", "identical"}: raise EnvironmentError("Release SHA is not on public main")
    prs = api.get(f"commits/{sha}/pulls?per_page=100")
    pr = next((p for p in prs if p.get("merged_at") and p.get("merge_commit_sha") == sha and p["base"]["ref"] == "main"), None)
    if not pr: raise EnvironmentError("Release must be an exact merged PR commit with traceable approval")
    approved = owner_evidence(api, pr["head"]["sha"])
    if not all(ci_result(api, pr["head"]["sha"]).values()): raise EnvironmentError("Approved PR head lacks successful required CI")
    return {"repository": api.repo, "source_pr": pr["number"], "merge_commit_sha": sha, **approved}


def gate(api, install=False):
    # Verify an actual main workflow before enabling its required status. The
    # existing v1 workflow is sufficient to bootstrap; PR 3 hardens its ref check.
    workflow = api.get("contents/.github/workflows/owner-release.yml?ref=main")
    code = base64.b64decode(workflow["content"]).decode()
    legacy = "Owner approval" in code and "pr.head.sha" in code
    hardened = False
    if "validateApproval" in code and "github.ref == 'refs/heads/main'" in code:
        helper = api.get("contents/.github/scripts/owner-approval.cjs?ref=main")
        policy = base64.b64decode(helper["content"]).decode()
        hardened = all(s in policy for s in ("context: 'Owner approval'", "actor !== owner", "ref !== 'refs/heads/main'", "pr.head.sha !== sha"))
    if not ("workflow_dispatch:" in code and "github.actor ==" in code and (legacy or hardened)):
        raise EnvironmentError("Trusted manual owner workflow is not installed on main")
    candidates = [r for r in api.get("rulesets") if r["target"] == "branch" and r["enforcement"] == "active"]
    found = None
    for candidate in candidates:
        rule = api.get(f"rulesets/{candidate['id']}")
        include = rule.get("conditions", {}).get("ref_name", {}).get("include", [])
        if "~DEFAULT_BRANCH" in include or "refs/heads/main" in include:
            found = rule
            break
    if found is None: raise EnvironmentError("No active main ruleset found; refusing to invent or replace protection")
    required = next((r for r in found["rules"] if r["type"] == "required_status_checks"), None)
    if required is None: raise EnvironmentError("Existing required CI checks must be configured first")
    contexts = {c["context"] for c in required["parameters"]["required_status_checks"]}
    if not set(CI_NAMES) <= contexts or found.get("bypass_actors"):
        raise EnvironmentError("Existing protection differs from the expected no-bypass CI policy")
    if install and OWNER_CONTEXT not in contexts:
        required["parameters"]["required_status_checks"].append({"context": OWNER_CONTEXT, "integration_id": ACTIONS_APP})
        payload = {k: found[k] for k in ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")}
        api.put(f"rulesets/{found['id']}", payload)
        found = api.get(f"rulesets/{found['id']}")
    checks = next(r["parameters"]["required_status_checks"] for r in found["rules"] if r["type"] == "required_status_checks")
    owner_required = any(c["context"] == OWNER_CONTEXT and c.get("integration_id") == ACTIONS_APP for c in checks)
    if install and not owner_required: raise EnvironmentError("Gate update did not become effective")
    return {"ruleset": found["id"], "owner_required": owner_required, "required_checks": checks,
            "workflow_hardened": hardened,
            "workflow_sha": workflow["sha"], "bypass_actors": found["bypass_actors"]}
