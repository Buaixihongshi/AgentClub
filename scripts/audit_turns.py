#!/usr/bin/env python3
"""审计回合：把"回复里的声明"和"事件流里的事实"对一遍（只读原型）。

目的不是打分，而是量出**可判定率**：一个回合里 agent 说的话，有多少能被
trace 证实/证伪，有多少根本无从判定。后者就是可观测性的缺口。

    .venv/bin/python scripts/audit_turns.py --limit 10
    .venv/bin/python scripts/audit_turns.py --mission m_xxx --verbose
    .venv/bin/python scripts/audit_turns.py --limit 50 --json > /tmp/audit.json

只读：以 `mode=ro` 打开 sqlite，不写任何东西，不调用模型。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

DB_DEFAULT = Path("data/yirenhub.sqlite3")

# ---------------------------------------------------------------------------
# 声明识别：句子 → 类别
# ---------------------------------------------------------------------------

# 优先级从高到低：一句里同时命中多个时，取最具体的那个。
CLAIM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("委派", re.compile(r"已委派|委派给|已通知|交给(冯宝宝|宝宝|楚岚|张楚岚|王也|baobao|chulan|wangye)|"
                        r"(让|请|叫)(冯宝宝|宝宝|楚岚|张楚岚|王也|baobao|chulan|wangye)"
                        r"[^，。；]{0,6}(复核|看|查|确认|处理|检查)")),
    ("产物", re.compile(r"已写入|写入了|写入到|保存到|已保存|生成(了)?(文件|报告|方案)|"
                        r"已创建|已产出|产出了|artifacts/")),
    ("执行", re.compile(r"已运行|运行了|执行了|跑了|已跑|测试通过|全部通过|已提交|已推送|"
                        r"已合并|已安装|已构建|构建通过|pytest")),
    ("核对", re.compile(r"核对了|核了一遍|核过一遍|核对出|核对过|查了代码|看了代码|读了代码|"
                        r"去代码里|逐条给位置")),
    ("完成", re.compile(r"已完成|已修复|已解决|搞定了|已处理|已经修好")),
]

# 明显的"计划/建议/假设"语气：是在说将来或应该，不是声称做过。
_PLAN = re.compile(r"建议|可以|应该|将会|下一步|如果|若|假如|一旦|需要|计划|打算|不妨|待|设想|原则是|预计")
# 明确的否定/诚实声明："我没有核对"这类不能当作虚假声明。
_DENIAL = re.compile(r"没有|没|未|无法|不能|尚未|并未")
_CONTRAST = re.compile(r"但|不过|却|而是")
# 只有过去/完成体（"了/过/已/完成"）或第一人称，才算"声称做过"。
_DONE_ASPECT = re.compile(r"已|了|过|完成|做到")

_SENT_SPLIT = re.compile(r"[。！？\n]+")
_MD = re.compile(r"[*_`#>]+")


def sentences(text: str) -> list[str]:
    out = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = _MD.sub("", raw).strip(" -—·")
        if len(s) >= 6:
            out.append(s)
    return out


def _negated(sentence: str, match: re.Match) -> bool:
    """命中的动词前面 20 字内有否定词，且没有转折——是"没做"，不是"做了"。"""
    head = sentence[max(0, match.start() - 20):match.start()]
    return bool(_DENIAL.search(head)) and not _CONTRAST.search(head)


def classify(sentence: str) -> str | None:
    """这句话声称做了什么，或 None（不是可核对的声明）。"""
    if _PLAN.search(sentence) or not _DONE_ASPECT.search(sentence):
        return None
    for name, pattern in CLAIM_PATTERNS:
        for match in pattern.finditer(sentence):
            if not _negated(sentence, match):
                return name
    return None


# ---------------------------------------------------------------------------
# 读取：mission → turn → 证据
# ---------------------------------------------------------------------------

OK, BAD, UNK = "ok", "bad", "unk"


@dataclass
class Tool:
    name: str
    summary: str = ""
    error: str = ""
    artifact_id: str | None = None


@dataclass
class A2A:
    kind: str  # invoke / skipped / reply / cancelled
    target: str = ""
    purpose: str = ""
    mode: str = ""
    reason: str = ""
    failed: bool = False


@dataclass
class Turn:
    mission_id: str
    turn_id: str
    agent_id: str = ""
    provider_id: str = ""
    purpose: str = ""
    status: str = ""
    duration_ms: int = 0
    usage: dict | None = None
    error: str = ""
    reply: str = ""
    tools: list[Tool] = field(default_factory=list)
    native: list[dict] = field(default_factory=list)   # provider.activity：CLI 自带工具
    a2a: list[A2A] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    claims: list[tuple[str, str, str, str]] = field(default_factory=list)  # (类别, 句子, 判定, 证据)

    @property
    def decisive(self) -> int:
        return sum(1 for c in self.claims if c[2] in (OK, BAD))

    @property
    def cost(self) -> float | None:
        return (self.usage or {}).get("total_cost_usd")


@dataclass
class Mission:
    id: str
    title: str
    status: str
    created_at: str
    kind: str
    mode: str = "task"
    turns: list[Turn] = field(default_factory=list)

    @property
    def claims(self) -> int:
        return sum(len(t.claims) for t in self.turns)


def _p(row_payload: str) -> dict:
    try:
        out = json.loads(row_payload or "{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def load(db_path: Path, limit: int, mission_id: str | None,
         mode: str | None = None) -> list[Mission]:
    if not db_path.exists():
        raise SystemExit(f"找不到数据库：{db_path}")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    missions: dict[str, Mission] = {}
    for row in db.execute("select id, data from missions"):
        data = _p(row["data"])
        missions[row["id"]] = Mission(
            id=row["id"],
            title=str(data.get("title") or data.get("goal") or "")[:40],
            status=str(data.get("status") or ""),
            created_at=str(data.get("created_at") or ""),
            kind=str(data.get("kind") or ""),
            mode=str(data.get("room_mode") or "task"),
        )

    turns: dict[str, Turn] = {}

    def _slot(m: Mission, tid: str) -> Turn:
        """同一 turn 可能 attempt 重试：原地替换，不重复出行。"""
        if tid in turns:
            old = turns[tid]
            t = Turn(m.id, tid, agent_id=old.agent_id, provider_id=old.provider_id,
                     purpose=old.purpose, reply=old.reply)
            m.turns[m.turns.index(old)] = t
            turns[tid] = t
            return t
        t = Turn(m.id, tid)
        turns[tid] = t
        m.turns.append(t)
        return t

    for row in db.execute("select id, mission_id, event_type, payload, created_at "
                          "from events order by id"):
        m = missions.get(row["mission_id"])
        p = _p(row["payload"])
        if m is None:
            continue
        et = row["event_type"]
        tid = p.get("turn_id") or ""

        if et == "turn.started" and tid:
            t = _slot(m, tid)
            t.agent_id = p.get("agent_id") or ""
            t.provider_id = p.get("provider_id") or ""
            t.purpose = str(p.get("purpose") or "")[:60]
        elif et in ("turn.completed", "turn.blocked", "turn.failed") and tid and tid in turns:
            t = turns[tid]
            t.status = et.split(".", 1)[1]
            t.duration_ms = int(p.get("duration_ms") or 0)
            t.error = str(p.get("error") or "")
        elif et == "turn.usage" and tid and tid in turns:
            turns[tid].usage = p.get("usage") or None
        elif et == "tool.call" and tid and tid in turns:
            turns[tid].tools.append(Tool(name=str(p.get("tool_name") or "")))
        elif et == "tool.result" and tid and tid in turns:
            name = str(p.get("tool_name") or "")
            for tool in reversed(turns[tid].tools):
                if tool.name == name and not tool.summary:
                    tool.summary = str(p.get("summary") or "")
                    tool.artifact_id = p.get("artifact_id")
                    break
        elif et == "tool.error" and tid and tid in turns:
            turns[tid].tools.append(Tool(name=str(p.get("tool_name") or ""),
                                         error=str(p.get("error") or "")))
        elif et in ("a2a.invoke", "a2a.skipped"):
            src = p.get("sender_turn_id") or ""
            a = A2A(kind=et.split(".")[1], target=str(p.get("target_agent_id") or ""),
                    purpose=str(p.get("purpose") or "")[:40], mode=str(p.get("mode") or ""),
                    reason=str(p.get("reason") or ""))
            if src in turns:
                turns[src].a2a.append(a)
        elif et == "a2a.reply" and tid:
            a = A2A(kind="reply", target=str(p.get("sender_agent_id") or ""),
                    failed=bool(p.get("failed")))
            if tid in turns:
                turns[tid].a2a.append(a)
        elif et == "provider.activity" and tid and tid in turns:
            turns[tid].native.append({"tool": str(p.get("tool_name") or ""),
                                      "phase": str(p.get("phase") or ""),
                                      "summary": str(p.get("summary") or "")})
        elif et == "artifact" and tid and tid in turns:
            turns[tid].artifacts.append({"path": p.get("path"), "title": p.get("title")})

    # 回复：该回合最后一条 agent 消息
    for row in db.execute("select data from messages"):
        d = _p(row["data"])
        tid = d.get("turn_id") or ""
        if d.get("role") == "agent" and tid in turns:
            turns[tid].reply = str(d.get("content") or "")

    for mid in (mission_id,):
        if mid and mid not in missions:
            raise SystemExit(f"找不到 mission：{mid}")

    pool = [missions[mission_id]] if mission_id else list(missions.values())
    if mode:
        pool = [m for m in pool if m.mode == mode]
    chosen = sorted(pool, key=lambda m: m.created_at, reverse=True)[:limit]
    for m in chosen:
        for t in m.turns:
            t.claims = judge_claims(t)
    return chosen


# ---------------------------------------------------------------------------
# 判定：一句话 → (类别, 句子, ok/bad/unk, 一句证据)
# ---------------------------------------------------------------------------

def _tool_names(turn: Turn) -> list[str]:
    return [t.name for t in turn.tools]


def _has_tool(turn: Turn, needles: tuple[str, ...]) -> str | None:
    for name in _tool_names(turn):
        low = name.lower()
        if any(n in low for n in needles):
            return name
    return None


def _native(turn: Turn, needles: tuple[str, ...]) -> list[str]:
    """CLI 自带工具里命中的那些（provider.activity 记下的）。"""
    hits = []
    for item in turn.native:
        low = item["tool"].lower()
        if any(n in low for n in needles) and item["tool"] not in hits:
            hits.append(item["tool"])
    return hits


def judge_claims(turn: Turn) -> list[tuple[str, str, str, str]]:
    out = []
    for sent in sentences(turn.reply):
        kind = classify(sent)
        if kind is None:
            continue
        out.append((kind, sent, *evidence(turn, kind, sent)))
    return out


def evidence(turn: Turn, kind: str, sent: str = "") -> tuple[str, str]:
    """这一类声明在本回合的事件流里能不能被判定。"""
    if kind == "委派":
        invoked = [a for a in turn.a2a if a.kind == "invoke"]
        if invoked:
            who = "、".join(a.target for a in invoked if a.target) or "下游"
            return OK, f"a2a.invoke → {who}"
        if _has_tool(turn, ("invoke_agent",)):
            return OK, "tool.call：invoke_agent"
        skipped = [a for a in turn.a2a if a.kind == "skipped"]
        if skipped:
            return BAD, f"a2a.skipped：{skipped[0].reason or '被吸收'}"
        return UNK, "无 a2a 事件（可能只是叙述，或走了未埋点的路径）"

    if kind == "产物":
        if turn.artifacts:
            return OK, f"artifact：{turn.artifacts[0].get('title') or turn.artifacts[0].get('path')}"
        name = _has_tool(turn, ("artifact", "write", "save"))
        if name:
            return OK, f"tool.call：{name}"
        native = _native(turn, ("write", "edit", "create", "replace"))
        if native:
            return OK, f"provider.activity：{', '.join(native)}（只知道写了，不知道写了什么）"
        return UNK, "无 artifact / write 事件"

    if kind == "执行":
        name = _has_tool(turn, ("exec", "shell", "bash", "run", "test", "pytest", "git"))
        if name:
            return OK, f"tool.call：{name}"
        native = _native(turn, ("run", "command", "exec", "shell", "terminal"))
        if native:
            return OK, f"provider.activity：{', '.join(native)}（无输出，不判结果）"
        if turn.tools:
            return UNK, f"只有 {', '.join(sorted(set(_tool_names(turn))))}；无执行类工具"
        return UNK, "本回合无工具调用"

    if kind == "核对":
        name = _has_tool(turn, ("read", "context", "grep", "search", "find", "file"))
        native = _native(turn, ("view_file", "read_file", "grep", "search", "find", "list_dir"))
        cites = re.search(r"[\w/]+\.(py|json|md|ts|tsx)(:\d+)?", sent)
        if native:
            return OK, f"provider.activity：{', '.join(native)}×{len(turn.native)}"
        if name:
            return OK, f"tool.call：{name}"
        why = "无读取类事件（该 provider 的原生工具未入 trace）"
        return UNK, why + ("；回复引用了具体文件/行号" if cites else "")

    if kind == "完成":
        if turn.status in ("blocked", "failed"):
            return BAD, f"回合状态是 {turn.status}"
        if turn.status == "completed":
            return OK, "回合状态是 completed"
        return UNK, "回合没有结束事件"

    return UNK, "未知类别"


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _cut(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _money(usd: float | None) -> str:
    if usd is None:
        return "$—"
    return f"${usd:,.4f}" if usd < 0.01 else f"${usd:,.2f}"


def _tokens(usage: dict | None) -> str:
    if not usage:
        return "—"
    return f"{int(usage.get('input_tokens') or 0) / 1000:.1f}k→{int(usage.get('output_tokens') or 0) / 1000:.1f}k"


def print_mission(m: Mission, verbose: bool) -> None:
    print(f"\n## {m.id}  {m.created_at[:16]}  [{m.status}/{m.mode}]  {m.title}")
    for t in m.turns:
        tools = ", ".join(dict.fromkeys(_tool_names(t))) or "—"
        native = ", ".join(dict.fromkeys(i["tool"] for i in t.native))
        if native:
            tools = f"{tools} +native:{native}"
        a2a = "、".join(f"{a.kind}→{a.target or '?'}" for a in t.a2a) or "—"
        flags = f"{t.decisive}/{len(t.claims)} 可判定"
        print(f"   {t.turn_id:<22} {t.agent_id:<8}/{t.provider_id:<12} {t.status:<9} "
              f"{t.duration_ms / 1000:6.1f}s {_tokens(t.usage):>12} {_money(t.cost):>10}  "
              f"tools: {_cut(tools, 34):<34} a2a: {_cut(a2a, 22):<22} claims: {flags}")
        if verbose:
            for kind, sent, verdict, why in t.claims:
                mark = {OK: "✔", BAD: "✘", UNK: "?"}[verdict]
                print(f"        {mark} [{kind}] {_cut(sent, 60)}")
                print(f"            → {why}")


def summarize(missions: list[Mission]) -> dict:
    turns = [t for m in missions for t in m.turns]
    claims = [c for t in turns for c in t.claims]
    by_kind: dict[str, dict[str, int]] = {}
    for kind, _, verdict, _ in claims:
        by_kind.setdefault(kind, {OK: 0, BAD: 0, UNK: 0})[verdict] += 1

    usage_turns = [t for t in turns if t.usage]
    cost_turns = [t for t in turns if t.cost is not None]
    summaries = [s for t in turns for s in (x.summary for x in t.tools) if s]
    native_by_provider: dict[str, list[int]] = {}
    for t in turns:
        bucket = native_by_provider.setdefault(t.provider_id or "?", [0, 0])
        bucket[1] += 1
        if t.native:
            bucket[0] += 1
    return {
        "missions": len(missions),
        "turns": len(turns),
        "replies": sum(1 for t in turns if t.reply),
        "turns_with_tools": sum(1 for t in turns if t.tools),
        "claims": len(claims),
        "ok": sum(1 for c in claims if c[2] == OK),
        "bad": sum(1 for c in claims if c[2] == BAD),
        "unk": sum(1 for c in claims if c[2] == UNK),
        "by_kind": by_kind,
        "turns_with_usage": len(usage_turns),
        "turns_with_cost": len(cost_turns),
        "tool_results": len(summaries),
        "summary_chars_median": statistics.median(len(s) for s in summaries) if summaries else 0,
        "placeholder_summaries": sum(1 for s in summaries if len(s) <= 40),
        "native_by_provider": {k: {"with": v[0], "turns": v[1]}
                               for k, v in sorted(native_by_provider.items())},
        "turns_with_native": sum(1 for t in turns if t.native),
    }


def print_summary(s: dict) -> None:
    total = s["claims"] or 1
    print("\n" + "=" * 78)
    print(f"扫描 {s['missions']} 个 mission / {s['turns']} 个回合 / {s['replies']} 条回复")
    print(f"可核对声明 {s['claims']} 句："
          f"证实 {s['ok']} ({s['ok'] / total:.0%}) · "
          f"证伪 {s['bad']} ({s['bad'] / total:.0%}) · "
          f"无法判定 {s['unk']} ({s['unk'] / total:.0%})")
    print(f"可判定率 {(s['ok'] + s['bad']) / total:.0%}")
    for kind, c in sorted(s["by_kind"].items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        print(f"  {kind:<4} {n:>3} 句  ✔{c[OK]:>3}  ✘{c[BAD]:>3}  ?{c[UNK]:>3}")
    print(f"\n工具输出：{s['tool_results']} 条 tool.result，"
          f"summary 中位数 {s['summary_chars_median']:.0f} 字符，"
          f"{s['placeholder_summaries']} 条 ≤40 字符")
    print(f"原生工具可见性：{s['turns_with_native']}/{s['turns']} 个回合有 provider.activity")
    for provider, c in s["native_by_provider"].items():
        print(f"  {provider:<12} {c['with']:>3}/{c['turns']:<3} 个回合可见原生工具")
    print(f"\n成本：{s['turns_with_usage']} 个回合有 usage，"
          f"其中只有 {s['turns_with_cost']} 个带 total_cost_usd"
          f"（{s['turns_with_cost'] / max(s['turns_with_usage'], 1):.0%} 覆盖）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="审计回合：回复声明 vs 事件流")
    ap.add_argument("--db", type=Path, default=DB_DEFAULT)
    ap.add_argument("--limit", type=int, default=10, help="最近多少个 mission")
    ap.add_argument("--mission", help="只看一个 mission")
    ap.add_argument("--mode", choices=("task", "roundtable"), help="只看某种房间模式")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印每句声明与判定")
    ap.add_argument("--json", action="store_true", help="只输出汇总 JSON")
    args = ap.parse_args(argv)

    missions = load(args.db, args.limit, args.mission, args.mode)
    if args.json:
        payload = {"summary": summarize(missions),
                   "missions": [{"id": m.id, "status": m.status, "title": m.title,
                                 "turns": [{"turn_id": t.turn_id, "agent": t.agent_id,
                                            "provider": t.provider_id, "status": t.status,
                                            "claims": [{"kind": k, "verdict": v, "why": w,
                                                        "sentence": s}
                                                       for k, s, v, w in t.claims]}
                                           for t in m.turns]} for m in missions]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    for m in missions:
        print_mission(m, args.verbose)
    print_summary(summarize(missions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
