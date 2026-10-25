import copy

import pytest

from scripts.environment_common import EnvironmentError
from scripts import release_policy as policy

SHA = "a" * 40
MERGE = "b" * 40


class API:
    repo = "owner/project"

    def __init__(self):
        self.writes = []
        self.compare_status = "identical"
        self.checks = [{"name": n, "app": {"id": policy.ACTIONS_APP}, "status": "completed", "conclusion": "success"}
                       for n in policy.CI_NAMES]
        self.pr = {"number": 1, "state": "open", "draft": False, "merged": False, "base": {"ref": "main"},
                   "head": {"sha": SHA}, "merged_at": "2026-10-22", "merge_commit_sha": MERGE}
        self.rules = {"id": 3, "name": "Protect main", "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [
                {"type": "deletion"}, {"type": "non_fast_forward"},
                {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
                {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": n, "integration_id": policy.ACTIONS_APP} for n in policy.CI_NAMES]}}]}

    def get(self, suffix):
        if "check-runs" in suffix: result = {"check_runs": self.checks}
        elif suffix == "git/ref/heads/main": result = {"object": {"sha": MERGE}}
        elif suffix.startswith("compare/"): result = {"status": self.compare_status}
        elif suffix.endswith("/pulls?per_page=100"): result = [self.pr]
        elif suffix == "pulls/1": result = self.pr
        elif suffix == "rulesets": result = [self.rules]
        elif suffix == "rulesets/3": result = self.rules
        else: raise AssertionError("Unexpected API read (owner workflow/status must not be required): " + suffix)
        return copy.deepcopy(result)

    def put(self, suffix, payload):
        self.writes.append((suffix, copy.deepcopy(payload)))
        self.rules.update(payload)
        return copy.deepcopy(self.rules)


def required(api):
    return next(r for r in api.rules["rules"] if r["type"] == "required_status_checks")["parameters"]


def test_gate_status_does_not_require_an_owner_workflow_or_write_settings():
    api = API()
    assert not policy.gate(api)["owner_required"]
    required(api)["required_status_checks"].append({"context": policy.OWNER_CONTEXT, "integration_id": policy.ACTIONS_APP})
    assert policy.gate(api)["owner_required"]
    assert not api.writes


def test_gate_removes_only_owner_context_preserving_every_other_setting():
    api = API()
    checks = required(api)["required_status_checks"]
    checks.extend([{"context": "Extra check", "integration_id": 42},
                   {"context": policy.OWNER_CONTEXT, "integration_id": policy.ACTIONS_APP},
                   {"context": policy.OWNER_CONTEXT, "integration_id": 42}])
    expected = copy.deepcopy(api.rules)
    required_before = expected["rules"][-1]["parameters"]["required_status_checks"]
    expected["rules"][-1]["parameters"]["required_status_checks"] = [
        c for c in required_before if c["context"] != policy.OWNER_CONTEXT]
    result = policy.gate(api, remove_owner=True)
    assert result["owner_required"] is False
    assert api.rules == expected
    assert len(api.writes) == 1 and api.writes[0][0] == "rulesets/3"
    policy.gate(api, remove_owner=True)
    assert len(api.writes) == 1


@pytest.mark.parametrize("problem", ["missing-ci", "untrusted-ci", "bypass", "not-strict", "missing-pr", "inactive"])
def test_removal_refuses_to_replace_unexpected_protection(problem):
    api = API()
    if problem == "missing-ci": required(api)["required_status_checks"].pop()
    elif problem == "untrusted-ci": required(api)["required_status_checks"][0]["integration_id"] = 42
    elif problem == "bypass": api.rules["bypass_actors"] = [{"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}]
    elif problem == "not-strict": required(api)["strict_required_status_checks_policy"] = False
    elif problem == "missing-pr": api.rules["rules"] = [r for r in api.rules["rules"] if r["type"] != "pull_request"]
    else: api.rules["enforcement"] = "disabled"
    with pytest.raises(EnvironmentError): policy.gate(api, remove_owner=True)
    assert not api.writes


def test_gate_readback_must_confirm_the_change():
    api = API()
    required(api)["required_status_checks"].append({"context": policy.OWNER_CONTEXT, "integration_id": policy.ACTIONS_APP})
    api.put = lambda *args: {}  # Simulate a remote update which did not persist.
    with pytest.raises(EnvironmentError, match="readback"):
        policy.gate(api, remove_owner=True)


def test_prepare_reports_current_head_ci_without_an_approval_dependency():
    api = API()
    result = policy.prepare(api, 1)
    assert result["head_sha"] == SHA and result["ready_for_review"] is True
    assert result["merged"] is False
    assert "owner_approval" not in result
    assert not api.writes


@pytest.mark.parametrize("problem", ["stacked", "draft", "closed", "ci-pending"])
def test_prepare_keeps_existing_review_prerequisites(problem):
    api = API()
    if problem == "stacked": api.pr["base"]["ref"] = "codex/dependency"
    elif problem == "draft": api.pr["draft"] = True
    elif problem == "closed": api.pr["state"] = "closed"
    else: api.checks[0].update(status="in_progress", conclusion=None)
    assert policy.prepare(api, 1)["ready_for_review"] is False


def test_squash_release_maps_ci_verified_head_to_actual_main_commit():
    api = API()
    result = policy.release_provenance(api, MERGE)
    assert result["verified_head_sha"] == SHA
    assert result["merge_commit_sha"] == MERGE and result["source_pr"] == 1
    assert all(result["ci"].values())
    assert "approved_head_sha" not in result and "approval_run" not in result


@pytest.mark.parametrize("problem", ["unmerged", "wrong-merge", "wrong-base"])
def test_release_requires_an_exact_merged_main_pr(problem):
    api = API()
    if problem == "unmerged": api.pr["merged_at"] = None
    elif problem == "wrong-merge": api.pr["merge_commit_sha"] = "c" * 40
    else: api.pr["base"]["ref"] = "codex/dependency"
    with pytest.raises(EnvironmentError, match="exact merged"):
        policy.release_provenance(api, MERGE)


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_release_must_be_on_public_main(status):
    api = API()
    api.compare_status = status
    with pytest.raises(EnvironmentError, match="public main"):
        policy.release_provenance(api, MERGE)


@pytest.mark.parametrize("problem", ["pending", "failed", "missing", "untrusted"])
def test_owner_removal_does_not_weaken_exact_head_ci(problem):
    api = API()
    if problem == "pending": api.checks[0].update(status="in_progress", conclusion=None)
    elif problem == "failed": api.checks[0]["conclusion"] = "failure"
    elif problem == "missing": api.checks.pop()
    else: api.checks[0]["app"]["id"] = 42
    with pytest.raises(EnvironmentError, match="required CI"):
        policy.release_provenance(api, MERGE)


def test_newer_ci_attempt_cannot_reuse_older_green_result():
    api = API()
    api.checks[0]["started_at"] = "2026-10-21"
    api.checks.append({**api.checks[0], "started_at": "2026-10-22", "status": "in_progress", "conclusion": None})
    assert not policy.ci_result(api, SHA)[policy.CI_NAMES[0]]
