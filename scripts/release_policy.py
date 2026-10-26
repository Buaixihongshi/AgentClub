"""Read exact-head CI and merged-PR provenance; preserve main protection."""
from __future__ import annotations

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


def prepare(api, number):
    pr = api.get(f"pulls/{int(number)}")
    ci = ci_result(api, pr["head"]["sha"])
    return {"pr": pr["number"], "head_sha": pr["head"]["sha"], "base": pr["base"]["ref"], "ci": ci,
            "ready_for_review": pr["state"] == "open" and not pr["draft"] and pr["base"]["ref"] == "main" and all(ci.values()),
            "merged": bool(pr.get("merged"))}


def release_provenance(api, sha):
    check_sha(sha)
    main = api.get("git/ref/heads/main")["object"]["sha"]
    compare = api.get(f"compare/{sha}...{main}")
    if compare["status"] not in {"ahead", "identical"}: raise EnvironmentError("Release SHA is not on public main")
    prs = api.get(f"commits/{sha}/pulls?per_page=100")
    pr = next((p for p in prs if p.get("merged_at") and p.get("merge_commit_sha") == sha and p["base"]["ref"] == "main"), None)
    if not pr: raise EnvironmentError("Release must be an exact merged PR commit with traceable CI")
    ci = ci_result(api, pr["head"]["sha"])
    if not all(ci.values()): raise EnvironmentError("Merged PR head lacks successful required CI")
    return {"repository": api.repo, "source_pr": pr["number"], "merge_commit_sha": sha,
            "verified_head_sha": pr["head"]["sha"], "ci": ci}


def gate(api, remove_owner=False):
    """Optionally remove only the retired owner check, then read back protection."""
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
    trusted = {c["context"] for c in required["parameters"]["required_status_checks"]
               if c.get("integration_id") == ACTIONS_APP}
    if (not set(CI_NAMES) <= trusted or found.get("bypass_actors")
            or not required["parameters"].get("strict_required_status_checks_policy")
            or not any(r["type"] == "pull_request" for r in found["rules"])):
        raise EnvironmentError("Existing protection differs from the expected no-bypass CI policy")
    if remove_owner and OWNER_CONTEXT in contexts:
        required["parameters"]["required_status_checks"] = [
            c for c in required["parameters"]["required_status_checks"] if c["context"] != OWNER_CONTEXT]
        payload = {k: found[k] for k in ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")}
        api.put(f"rulesets/{found['id']}", payload)
        found = api.get(f"rulesets/{found['id']}")
        if any(found.get(k) != v for k, v in payload.items()):
            raise EnvironmentError("Gate readback differs from the requested preserving update")
    checks = next(r["parameters"]["required_status_checks"] for r in found["rules"] if r["type"] == "required_status_checks")
    owner_required = any(c["context"] == OWNER_CONTEXT for c in checks)
    if remove_owner and owner_required: raise EnvironmentError("Owner check removal did not become effective")
    return {"ruleset": found["id"], "owner_required": owner_required, "required_checks": checks,
            "bypass_actors": found["bypass_actors"]}
