"""Executable checks for YiRen Hub architecture iron laws (L1–L4).

See docs/architecture/iron-laws.md.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from backend.app.adapters.mcp_subprocess import MCPSubprocessAdapter
from backend.app.domain.models import Conversation, Mission, TurnStatus
from backend.app.mcp.tools import MCPTools
from backend.app.settings import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def seeded_mission(repository):  # noqa: ANN201
    conv = Conversation(id="c_iron", title="iron")
    repository.create_conversation(conv)
    mission = Mission(
        id="m_iron",
        title="Test",
        goal="iron law regression",
        conversation_id=conv.id,
    )
    repository.create_mission(mission)
    return mission


def test_l1_stdout_does_not_become_agent_message(
    tmp_path: Path,
    repository,
    temp_settings,
    seeded_mission,
) -> None:
    script = tmp_path / "stdout_only_cli.py"
    script.write_text(
        "\n".join(
            [
                "# -*- coding: utf-8 -*-",
                "import sys",
                'if __name__ == "__main__":',
                '    sys.stdout.write("我已经回复了\\n")',
                "",
            ]
        ),
        encoding="utf-8",
    )
    tools = MCPTools(repository, temp_settings.artifact_root)
    adapter = MCPSubprocessAdapter(
        command=sys.executable,
        args=[str(script)],
        db_path=temp_settings.database_path,
        artifact_root=temp_settings.artifact_root,
        timeout=10,
    )
    outcome = adapter.run_turn(
        seeded_mission.id,
        "baobao",
        "t_l1",
        "codex",
        "请发声",
        tools,
    )
    assert outcome.status == TurnStatus.BLOCKED
    assert outcome.summary == "agent did not call publish_message via MCP"
    events = repository.list_events_after(seeded_mission.id, 0)
    agent_msgs = [
        e
        for e in events
        if e.event_type == "message" and (e.payload or {}).get("role") == "agent"
    ]
    assert not agent_msgs, "stdout must not synthesise agent message events"


def _is_os_path_expanduser(call_node: ast.Call) -> str | None:
    func = call_node.func
    if isinstance(func, ast.Attribute) and func.attr == "expanduser":
        recv = func.value
        if isinstance(recv, ast.Attribute) and recv.attr == "path":
            base = getattr(recv.value, "id", "")
            if base == "os":
                arg0 = call_node.args[0] if call_node.args else None
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    return arg0.value
    if isinstance(func, ast.Name) and func.id == "expanduser":
        arg0 = call_node.args[0] if call_node.args else None
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            return arg0.value
    return None


def _mentions_user_home_cli_marker(s: str) -> bool:
    if "~/.codex" in s:
        return True
    if "~/.gemini" in s:
        return True
    norm = s.replace("\\", "/")
    return "~/.claude.json" in norm


def _literal_or_expanduser_writes_user_home(subtree: ast.AST | None) -> bool:
    if subtree is None:
        return False
    for child in ast.walk(subtree):
        if isinstance(child, ast.Call):
            lit = _is_os_path_expanduser(child)
            if lit is not None and _mentions_user_home_cli_marker(lit):
                return True
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            if _mentions_user_home_cli_marker(child.value):
                return True
    return False


def _is_writing_open(open_call: ast.Call) -> bool:
    if not isinstance(open_call.func, ast.Name) or open_call.func.id != "open":
        return False
    mode = ""
    if len(open_call.args) >= 2:
        snd = open_call.args[1]
        if isinstance(snd, ast.Constant) and isinstance(snd.value, str):
            mode = snd.value
    for kw in open_call.keywords or []:
        if kw.arg == "mode":
            kv = getattr(kw, "value", None)
            if isinstance(kv, ast.Constant) and isinstance(kv.value, str):
                mode = kv.value
    if not mode:
        return False
    return bool(set(mode) & set("wax"))


def test_l2_no_writes_to_user_cli_home() -> None:
    app_py = _REPO_ROOT / "backend" / "app"
    seed_expand_errors: list[str] = []
    write_target_errors: list[str] = []

    for py_path in sorted(app_py.rglob("*.py")):
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        caller_stack: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                caller_stack.append(node.name)
                self.generic_visit(node)
                caller_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                caller_stack.append(node.name)
                self.generic_visit(node)
                caller_stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                literal = _is_os_path_expanduser(node)
                if literal is not None and _mentions_user_home_cli_marker(literal):
                    enclosing = caller_stack[-1] if caller_stack else "<module>"
                    if not enclosing.startswith("_seed_"):
                        seed_expand_errors.append(
                            "{path}:{lineno} expanduser({lit!r}) outside _seed_* (in {fn})".format(
                                path=os.path.relpath(py_path, _REPO_ROOT),
                                lineno=node.lineno,
                                lit=literal,
                                fn=enclosing,
                            ),
                        )

                write_target = None
                func = node.func

                if _is_writing_open(node) and node.args:
                    write_target = node.args[0]
                elif isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
                    write_target = func.value

                shutil_move_like = isinstance(func, ast.Attribute) and getattr(func.value, "id", None) == "shutil"
                if shutil_move_like and func.attr in (
                    "copy",
                    "copy2",
                    "copyfile",
                    "copytree",
                    "move",
                ):
                    if len(node.args) >= 2:
                        write_target = node.args[1]

                if write_target is not None and _literal_or_expanduser_writes_user_home(write_target):
                    enclosing = caller_stack[-1] if caller_stack else "<module>"
                    write_target_errors.append(
                        "{path}:{lineno} write destination references ~/.codex|.claude.json|~/.gemini (in {fn})".format(
                            path=os.path.relpath(py_path, _REPO_ROOT),
                            lineno=node.lineno,
                            fn=enclosing,
                        ),
                    )

                self.generic_visit(node)

        Visitor().visit(tree)

    assert write_target_errors == [], "\n".join(write_target_errors)
    assert seed_expand_errors == [], "\n".join(seed_expand_errors)


def _scan_regex_mention_patterns(tree: ast.Module, path_rel: str) -> list[str]:
    """Flag `re` calls whose regexp pattern literals contain '@' (mention-style routing heuristic)."""

    re_aliases = {"re"}
    imported_from_re: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == "re":
                    re_aliases.add(alias.asname or "re")
        elif isinstance(stmt, ast.ImportFrom) and stmt.module == "re":
            for alias in stmt.names:
                if alias.name != "*":
                    imported_from_re.add(alias.asname or alias.name)

    hits: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        pat_expr = None
        method_name = ""

        if isinstance(func, ast.Name):
            nm = func.id
            method_name = nm
            if nm in imported_from_re and nm == "sub" and len(node.args) >= 2:
                pat_expr = node.args[1]
            elif nm in imported_from_re and nm in (
                "compile",
                "search",
                "match",
                "findall",
                "finditer",
                "fullmatch",
            ):
                pat_expr = node.args[0] if node.args else None
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            method_name = getattr(func, "attr", "") or ""
            if func.value.id not in re_aliases:
                continue
            if method_name == "sub" and len(node.args) >= 2:
                pat_expr = node.args[1]
            elif (
                method_name in ("compile", "search", "match", "findall", "finditer", "fullmatch")
                and node.args
            ):
                pat_expr = node.args[0]
        else:
            continue

        if pat_expr is None:
            continue

        fragments: list[str] = []

        def _fragments_collect(n: ast.AST) -> None:
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                fragments.append(n.value)
            elif isinstance(n, ast.JoinedStr):
                for part in getattr(n, "values", []):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        fragments.append(part.value)

        _fragments_collect(pat_expr)

        joined = "".join(fragments)
        if "@" not in joined:
            continue

        hits.append(
            "{p}:{lineno} re regexp pattern mentions '@' (method={meth})".format(
                p=path_rel,
                lineno=node.lineno,
                meth=method_name,
            ),
        )

    return hits


def _scan_split_and_startswith(tree: ast.Module, path_rel: str) -> list[str]:
    hits: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        if isinstance(func, ast.Attribute) and func.attr == "split" and node.args:
            fst = node.args[0]
            if isinstance(fst, ast.Constant) and fst.value == "@":
                hits.append(
                    "{p}:{lineno} split('@')".format(p=path_rel, lineno=node.lineno),
                )

        if isinstance(func, ast.Attribute) and func.attr == "startswith":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "@":
                    hits.append(
                        "{p}:{lineno} startswith('@')".format(p=path_rel, lineno=node.lineno),
                    )
                if isinstance(arg, (ast.Tuple, ast.List)):
                    for elt in arg.elts:
                        if isinstance(elt, ast.Constant) and elt.value == "@":
                            hits.append(
                                "{p}:{lineno} startswith(...) tuple contains '@'".format(
                                    p=path_rel,
                                    lineno=node.lineno,
                                ),
                            )

    return hits


def test_l3_a2a_only_via_invoke_agent() -> None:
    orchestration = _REPO_ROOT / "backend" / "app" / "orchestration"
    api = _REPO_ROOT / "backend" / "app" / "api"
    paths = sorted(orchestration.rglob("*.py")) + sorted(api.rglob("*.py"))
    routing_patterns: list[str] = []

    for py_path in paths:
        if py_path.name == "speaker.py":
            continue
        rel = os.path.relpath(py_path, _REPO_ROOT)
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        routing_patterns.extend(_scan_split_and_startswith(tree, rel))
        routing_patterns.extend(_scan_regex_mention_patterns(tree, rel))

    assert routing_patterns == [], "routing via @-text heuristic:\n" + "\n".join(routing_patterns)

    tools_path = _REPO_ROOT / "backend" / "app" / "mcp" / "tools.py"
    tools_tree = ast.parse(tools_path.read_text(encoding="utf-8"), filename=str(tools_path))
    invoke_lo: int | None = None
    invoke_hi: int | None = None

    for blk in getattr(tools_tree, "body", []):
        if isinstance(blk, ast.ClassDef) and blk.name == "MCPTools":
            for member in blk.body:
                if isinstance(member, ast.FunctionDef) and member.name == "invoke_agent":
                    invoke_lo = member.lineno
                    end_lineno = getattr(member, "end_lineno", None)
                    if isinstance(end_lineno, int):
                        invoke_hi = end_lineno
                    else:
                        hi = member.lineno
                        for descendant in ast.walk(member):
                            if getattr(descendant, "lineno", None):
                                hi = max(hi, int(descendant.lineno))
                        invoke_hi = hi
                    break

    assert invoke_lo is not None and invoke_hi is not None, "MCPTools.invoke_agent not found"

    bad_enqueue: list[str] = []
    for node in ast.walk(tools_tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "a2a_enqueue":
            if isinstance(func.value, ast.Name) and func.value.id == "self":
                ln = getattr(node, "lineno", None)
                if ln is None or not (invoke_lo <= int(ln) <= invoke_hi):
                    bad_enqueue.append(
                        "{rel}:{lineno} self.a2a_enqueue outside invoke_agent".format(
                            rel=os.path.relpath(tools_path, _REPO_ROOT),
                            lineno=ln,
                        ),
                    )

    assert bad_enqueue == [], "\n".join(bad_enqueue)


def test_l4_fake_default_real_providers_disabled() -> None:
    assert Settings().use_fake_cli is True
    cfg = _REPO_ROOT / "config" / "cli-providers.json"
    providers = json.loads(cfg.read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in providers}
    assert by_id["fake"]["enabled"] is True
    assert by_id["claude"]["enabled"] is False
    assert by_id["codex"]["enabled"] is False
    assert by_id["antigravity"]["enabled"] is False
    assert "gemini" not in by_id
