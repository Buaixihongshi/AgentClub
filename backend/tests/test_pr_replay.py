import pytest

from scripts.prepare_pr_pilot import qualification_passes, read_junit


def test_junit_keeps_setup_errors_distinct_from_assertion_failures(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text('''<testsuites><testsuite>
      <testcase name="bug"><failure message="assertion" /></testcase>
      <testcase name="dependency"><error message="missing dependency" /></testcase>
      <testcase name="not_run"><skipped /></testcase>
      <testcase name="regression" />
    </testsuite></testsuites>''')
    assert read_junit(report) == {"bug": "failed", "dependency": "error", "not_run": "skipped", "regression": "passed"}


@pytest.mark.parametrize("change", ["none", "setup_error", "skipped_bug", "regression_broken", "missing_result", "harness_error", "no_bug_checks"])
def test_qualification_requires_real_failure_then_pass_and_preserved_regressions(change):
    task = {"fail_to_pass": ["bug"], "pass_to_pass": ["stable"]}
    base = {"returncode": 1, "checks": {"bug": "failed", "stable": "passed"}}
    reference = {"returncode": 0, "checks": {"bug": "passed", "stable": "passed"}}
    if change == "setup_error":
        base["checks"]["bug"] = "error"
    elif change == "skipped_bug":
        base["checks"]["bug"] = "skipped"
    elif change == "regression_broken":
        reference["checks"]["stable"] = "failed"
    elif change == "missing_result":
        del reference["checks"]["bug"]
    elif change == "harness_error":
        base["returncode"] = 2
    elif change == "no_bug_checks":
        task["fail_to_pass"] = []
        del base["checks"]["bug"]
        del reference["checks"]["bug"]
    assert bool(qualification_passes(task, base, reference)) == (change == "none")
