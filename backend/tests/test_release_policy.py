import base64
import copy
from pathlib import Path
import subprocess

import pytest

from scripts.environment_common import EnvironmentError
from scripts import release_policy as policy

SHA = "a" * 40
MERGE = "b" * 40


class API:
    repo = "owner/project"
    def __init__(self):
        self.writes = []
        self.statuses = [{"context": policy.OWNER_CONTEXT, "state": "success", "created_at": "2026-10-22T00:00:00Z",
            "target_url": "https://github.com/owner/project/actions/runs/1", "creator": {"login": "github-actions[bot]"}}]
        self.checks = [{"name": n, "app": {"id": policy.ACTIONS_APP}, "status": "completed", "conclusion": "success"} for n in policy.CI_NAMES]
        self.pr = {"number": 1, "state": "open", "draft": False, "merged": False, "base": {"ref": "main"},
                   "head": {"sha": SHA}, "merged_at": "2026-10-22", "merge_commit_sha": MERGE}
        self.rules = {"id": 3, "name": "Protect main", "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [
                {"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
                {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": n, "integration_id": policy.ACTIONS_APP} for n in policy.CI_NAMES]}}]}

    def get(self, suffix):
        if "check-runs" in suffix: result = {"check_runs": self.checks}
        elif suffix.endswith("/status"): result = {"statuses": self.statuses if SHA in suffix else []}
        elif suffix.startswith("actions/runs/"):
            result = {"event": "workflow_dispatch", "head_branch": "main", "actor": {"login": "owner"},
                      "conclusion": "success", "path": ".github/workflows/owner-release.yml"}
        elif suffix == "git/ref/heads/main": result = {"object": {"sha": MERGE}}
        elif suffix.startswith("compare/"): result = {"status": "identical"}
        elif suffix.endswith("/pulls?per_page=100"): result = [self.pr]
        elif suffix == "pulls/1": result = self.pr
        elif suffix.startswith("contents/"):
            code = "workflow_dispatch:\nif: github.actor == 'owner'\npr.head.sha\nOwner approval"
            result = {"sha": "workflow-sha", "content": base64.b64encode(code.encode()).decode()}
        elif suffix == "rulesets": result = [self.rules]
        elif suffix == "rulesets/3": result = self.rules
        else: raise AssertionError(suffix)
        return copy.deepcopy(result)

    def put(self, suffix, payload):
        self.writes.append((suffix, copy.deepcopy(payload)))
        self.rules.update(payload)
        return self.rules


def test_gate_installs_only_owner_context_preserving_existing_protection():
    api = API()
    before = copy.deepcopy(api.rules)
    assert not policy.gate(api)["owner_required"]
    assert not api.writes
    result = policy.gate(api, install=True)
    assert result["owner_required"]
    assert len(api.writes) == 1 and api.writes[0][0] == "rulesets/3"
    assert api.rules["rules"][:3] == before["rules"][:3]
    assert api.rules["conditions"] == before["conditions"]
    assert api.rules["bypass_actors"] == []
    assert api.rules["rules"][-1]["parameters"]["strict_required_status_checks_policy"]
    policy.gate(api, install=True)
    assert len(api.writes) == 1


def test_stale_or_untrusted_approvals_do_not_authorize_release():
    api = API()
    assert policy.owner_evidence(api, SHA)["approved_head_sha"] == SHA
    with pytest.raises(EnvironmentError): policy.owner_evidence(api, "c" * 40)
    api.statuses[0]["creator"]["login"] = "owner"
    with pytest.raises(EnvironmentError, match="trusted"): policy.owner_evidence(api, SHA)
    api.statuses[0]["creator"]["login"] = "github-actions[bot]"
    api.statuses[0]["target_url"] = "https://github.com/other/repo/actions/runs/1"
    with pytest.raises(EnvironmentError): policy.owner_evidence(api, SHA)


def test_squash_release_maps_approved_head_to_actual_main_commit():
    api = API()
    result = policy.release_provenance(api, MERGE)
    assert result["approved_head_sha"] == SHA
    assert result["merge_commit_sha"] == MERGE
    assert result["source_pr"] == 1
    api.pr["merge_commit_sha"] = "c" * 40
    with pytest.raises(EnvironmentError, match="exact merged"): policy.release_provenance(api, MERGE)


def test_stacked_pr_is_not_ready_for_owner_main_approval():
    api = API()
    api.pr["base"]["ref"] = "codex/dependency"
    assert policy.prepare(api, 1)["ready_for_owner_review"] is False


def test_newer_ci_attempt_cannot_reuse_older_green_result():
    api = API()
    api.checks[0]["started_at"] = "2026-10-21"
    api.checks.append({**api.checks[0], "started_at": "2026-10-22", "status": "in_progress", "conclusion": None})
    assert not policy.ci_result(api, SHA)[policy.CI_NAMES[0]]


def test_owner_workflow_offline_policy():
    subprocess.run(["node", "--test", ".github/scripts/owner-approval.test.cjs"], check=True, capture_output=True)


def test_gate_recognizes_the_hardened_workflow_and_its_main_policy_helper():
    api = API()
    original = api.get
    def get(suffix):
        if suffix.startswith("contents/"):
            filename = suffix.removeprefix("contents/").split("?", 1)[0]
            return {"sha": "test", "content": base64.b64encode(Path(filename).read_bytes()).decode()}
        return original(suffix)
    api.get = get
    assert policy.gate(api)["workflow_hardened"] is True
