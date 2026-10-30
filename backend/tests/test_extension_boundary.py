"""Core modules reach the optional extensions only through extension_points."""
import ast
from pathlib import Path

from backend.app import extension_points

APP = Path(__file__).resolve().parents[1] / "app"
EXTENSIONS = "backend.app.extensions"
# The composition root wires extensions into the app; nothing else in core may.
COMPOSITION_ROOTS = {APP / "main.py"}


def _core_modules():
    for path in sorted(APP.rglob("*.py")):
        if (APP / "extensions") in path.parents or path in COMPOSITION_ROOTS:
            continue
        yield path


def _extension_references(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # importlib/patch targets spelled as strings count as imports too.
            names = [node.value]
        else:
            continue
        for name in names:
            if name == EXTENSIONS or name.startswith(EXTENSIONS + "."):
                yield node.lineno, name


def test_core_modules_do_not_import_extensions():
    offenders = [f"{path.relative_to(APP)}:{line} {name}"
                 for path in _core_modules() for line, name in _extension_references(path)]
    assert not offenders, "core imports an extension directly:\n" + "\n".join(offenders)


def test_boundary_scan_sees_the_core_and_catches_imports(tmp_path):
    scanned = {path.relative_to(APP).as_posix() for path in _core_modules()}
    assert {"orchestration/runner.py", "mcp/tools.py", "mcp/server.py", "api/missions.py"} <= scanned
    assert not any(name.startswith("extensions/") for name in scanned)
    probe = tmp_path / "probe.py"
    probe.write_text("from backend.app import extensions\n"
                     "from backend.app.extensions.tasks.service import SharedWork\n"
                     "import importlib; importlib.import_module('backend.app.extensions.discussion')\n")
    assert [name for _line, name in _extension_references(probe)] == [
        "backend.app.extensions", "backend.app.extensions.tasks.service",
        "backend.app.extensions.tasks.service.SharedWork", "backend.app.extensions.discussion"]


def test_mcp_subprocess_receives_installed_extensions(temp_settings):
    from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
    adapter = MCPSubprocessAdapter("codex", db_path=temp_settings.database_path,
                                   artifact_root=temp_settings.artifact_root)
    env = adapter._mcp_env("m", "a", "t", "codex")
    assert env["YIREN_MCP_EXTENSIONS"].split(",") == extension_points.modules() == [EXTENSIONS]


def test_unknown_hook_names_fail_loudly():
    import pytest
    with pytest.raises(KeyError):
        extension_points.register("not_a_hook", lambda: None)
    with pytest.raises(KeyError):
        extension_points.first("not_a_hook")
