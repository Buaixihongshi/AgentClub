"""Bounded real-CLI protocol and help-seeking probes, separate from capability scores."""
from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.adapters.base import TurnOutcome
from backend.app.domain.agent_registry import AgentRegistry
from backend.app.domain.models import AgentProfile, Conversation, Message, Mission, MissionStatus, OrchestrationConfig, OrchestrationMode, TurnStatus
from backend.app.evaluation.real_pilot import BudgetedAdapter, IsolatedCLI, Ledger, PACK, ROOT
from backend.app.evaluation.scoring import score_answer
from backend.app.evaluation.tasks import Rubric, RubricCheck
from backend.app.orchestration.runner import MissionRunner
from backend.app.orchestration.speaker import WorkflowStep
from backend.app.orchestration.strategies import OrchestrationPlan
from backend.app.settings import Settings
from backend.app.storage.repository import Repository


@dataclass(frozen=True)
class Probe:
    id: str
    kind: str
    lead_model: str
    peer_model: str

    @property
    def max_host_calls(self):
        return 1 if self.kind == "direct" else 3


PROBES = [Probe(f"{kind}-{lead}", kind, lead, peer)
          for kind in ("protocol", "autonomous", "direct")
          for lead, peer in (("economy", "strong"), ("strong", "economy"))]


class ProbeRunner(MissionRunner):
    def _plan_orchestration(self, mission):
        # Only the lead is scheduled. Every peer turn must come from a real tool request.
        return OrchestrationPlan(OrchestrationMode.DYNAMIC,
                                 [WorkflowStep("chulan", "根据已知信息自主决定下一步并完成任务")],
                                 ["chulan", "baobao"], 1, 1)


class ProbeLedger(Ledger):
    def before(self, model_key):
        # CLI overhead exceeded the old 12k assumption; keep generous next-call headroom.
        reservation = self.cost(model_key, {"input_tokens": 100000, "output_tokens": 8000})
        if self.stop_reason:
            raise RuntimeError(self.stop_reason)
        if self.spent + reservation > self.limit:
            self.stop_reason = "协议探针下一次调用费用预留不足"
            raise RuntimeError(self.stop_reason)


def goal_for(probe, receipt):
    goal = (
        "核验明日工单计划：待完成工单 84 件，3 人，每人工作 7 小时。"
        "团队成员：chulan 是本次请求者，baobao 是本次记录核验搭档。"
        "最终仅提交 JSON：{\"capacity\":整数,\"can_finish\":布尔值,\"receipt\":原始签名回执}。"
        "capacity=人数×每人小时数×每人每小时处理量；can_finish 表示容量是否至少为 84。"
    )
    if probe.kind == "direct":
        goal += f"本题资料齐全：每人每小时处理量为 5，签名回执为 {receipt}。"
    else:
        goal += "当前公开资料没有处理速率及签名回执；baobao 持有这两项已核验记录，可回答查询。不得猜测未见数据。"
    goal += "你可以直接交付、读取已有对话、请求搭档帮助或说明信息不足，按实际需要选择。"
    if probe.kind == "protocol":
        goal += (
            "这是明确要求求助的协议探针：第一轮必须调用 "
            'invoke_agent(target_agent_id="baobao", purpose="核验处理速率和签名回执", mode="wait_reply")。'
            "如果已经处于【接续上一轮】，不要再次邀请，使用收到的回复提交结果。"
        )
    return goal


class ProbeAdapter(BudgetedAdapter):
    def __init__(self, runner, ledger, condition, probe, output, receipt, factory=None):
        self.probe, self.output, self.receipt = probe, output, receipt
        self.host_calls = 0
        self.factory_override = factory
        super().__init__(runner, ledger, condition, factory=self._factory)

    def _factory(self, *args, **kwargs):
        return (self.factory_override or IsolatedCLI)(*args, **kwargs)

    def run_turn(self, mission_id, agent_id, turn_id, provider_id, prompt, tools):
        if self.host_calls >= self.probe.max_host_calls or len(self.ledger.calls) >= 14:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary="探针宿主调用上限；未启动 CLI")
        try:
            self.ledger.before(self.condition["mapping"][agent_id])
        except RuntimeError as exc:
            return TurnOutcome(status=TurnStatus.BLOCKED, summary=str(exc))
        if agent_id == "baobao":
            prompt += (
                "\n【仅提供给记录核验搭档的封存资料】每人每小时处理量=5；"
                f"签名回执={self.receipt}。只发布这两项原始记录的 JSON，"
                "字段为 rate_per_person_hour 和 receipt；不要再次邀请，不要代替请求者提交最终工单计划。"
            )
        elif "\n【接续上一轮】" in prompt:
            prompt += "\n【本轮状态】已收到搭档回复；不要再次邀请。根据该回复提交最终 JSON。"
        else:
            prompt += (
                "\n【通信约定】如决定请求帮助，先 publish_message 简短说明正在核验，"
                "再调用工具并结束当前回合；需要对方结果后继续时用 wait_reply。"
                "不要以正文点名代替工具，不要提交猜测的数据。log_thought 只需简短判断摘要。"
            )
        prompts = self.output / "prompts"
        prompts.mkdir(exist_ok=True)
        (prompts / f"{turn_id}.json").write_text(json.dumps({
            "agent_id": agent_id, "provider_id": provider_id, "prompt": prompt,
            "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }, ensure_ascii=False, indent=2) + "\n")
        self.host_calls += 1
        return super().run_turn(mission_id, agent_id, turn_id, provider_id, prompt, tools)


def assess(repository, mission, probe, output, expected, actual_host_calls):
    events = repository.list_events_after(mission.id, 0)
    counts = Counter(event.event_type for event in events)
    messages = [message for message in repository.list_messages(mission.id) if message.role == "agent"]
    lead = [message for message in messages if message.agent_id == "chulan"]
    answer = lead[-1].content if lead else ""
    correct = score_answer(answer, Rubric(threshold=1, checks=[RubricCheck(
        id="final", kind="json_exact", value=expected)])).passed
    tools = [event.payload for event in events if event.event_type == "evaluation.tools_list"]
    descriptors = [tool for event in tools for tool in event["tools"] if tool["name"] == "invoke_agent"]
    schema_ok = bool(descriptors) and all("wait_reply" in tool["inputSchema"]["properties"]["mode"]["enum"] for tool in descriptors)
    invokes = [event for event in events if event.event_type == "a2a.invoke"]
    replies = [event for event in events if event.event_type == "a2a.reply"]
    resumed = [json.loads(path.read_text()) for path in (output / "prompts").glob("*.json")
               if "\n【接续上一轮】" in json.loads(path.read_text())["prompt"]]
    peer_messages = [message for message in messages if message.agent_id == "baobao"]
    linked_reply = bool(len(invokes) == len(replies) == len(peer_messages) == 1
        and str(replies[0].payload["invoke_event_id"]) == str(invokes[0].id)
        and replies[0].payload["reply_message_id"] == peer_messages[0].id
        and replies[0].payload["turn_id"] == peer_messages[0].turn_id)
    reply_visible = bool(linked_reply and len(resumed) == 1
                         and replies[0].payload["reply_text"] in resumed[0]["prompt"])
    requested = sum(event.event_type == "tool.call" and event.payload.get("tool_name") == "invoke_agent" for event in events)
    roundtrip = bool(schema_ok and counts["a2a.request"] == 1 and linked_reply and reply_visible
                     and invokes[0].payload.get("mode") == "wait_reply" and actual_host_calls == 3)
    if probe.kind == "direct":
        passed = schema_ok and correct and requested == 0 and actual_host_calls == 1
    else:
        passed = roundtrip and correct
    return {
        "id": probe.id, "kind": probe.kind, "lead_model": probe.lead_model, "peer_model": probe.peer_model,
        "mission_id": mission.id, "mission_status": str(repository.get_mission(mission.id).status),
        "passed": bool(passed and repository.get_mission(mission.id).status == MissionStatus.COMPLETED),
        "schema_wait_reply_visible": schema_ok, "final_correct": correct,
        "invoke_tool_calls": requested, "a2a_requests": counts["a2a.request"], "a2a_invokes": len(invokes),
        "linked_reply": linked_reply, "reply_in_resume_prompt": reply_visible, "roundtrip_verified": roundtrip,
        "host_calls": actual_host_calls,
        "public_messages": [{"agent_id": message.agent_id, "turn_id": message.turn_id, "content": message.content} for message in messages],
        "a2a_events": [{"id": event.id, "type": event.event_type, "payload": event.payload} for event in events if event.event_type.startswith("a2a.")],
    }


def run_probes(output, *, execute=False, limit_usd=1.5, timeout=120, factory=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise ValueError("Use a fresh probe output directory")
    protocol = json.loads((PACK / "protocol.json").read_text())
    manifest = {
        "mode": "real" if execute else "dry-run", "status": "planned", "limit_usd": limit_usd,
        "hard_spend_limit": False, "max_host_calls": 14, "account_invoice_usd": None,
        "models": protocol["models"], "pricing_scope": "Frozen standard API-equivalent rates from cost-aware-v1; not a CLI invoice",
        "plan": [probe.__dict__ for probe in PROBES], "results": [],
        "scope": "Directed protocol, information-gap help seeking, and complete-information negative controls; not a coding capability benchmark",
    }
    files = [Path(__file__), ROOT / "backend/app/mcp/server.py", ROOT / "backend/app/orchestration/runner.py",
             ROOT / "backend/app/evaluation/real_pilot.py", ROOT / "backend/app/evaluation/pilot_mcp_server.py",
             ROOT / "backend/app/mcp/tools.py", ROOT / "backend/app/orchestration/events.py",
             ROOT / "backend/app/adapters/mcp_subprocess.py", ROOT / "backend/app/orchestration/speaker.py"]
    manifest["execution_files"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    manifest_path = output / "manifest.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    save()
    if not execute:
        return manifest
    ledger = ProbeLedger(output, manifest["models"], limit_usd)
    manifest["status"] = "running"
    for probe in PROBES:
        trial_dir = output / probe.id
        trial_dir.mkdir()
        settings = Settings(database_path=trial_dir / "eval.sqlite3", artifact_root=trial_dir / "artifacts",
                            config_dir=ROOT / "config", use_fake_cli=False, max_turns=1,
                            max_a2a_invocations=1, cli_timeout=timeout)
        settings.artifact_root.mkdir()
        repository = Repository(settings.database_path)
        repository.initialize()
        receipt = secrets.token_hex(12)
        expected = {"capacity": 105, "can_finish": True, "receipt": receipt}
        (trial_dir / "private_expectation.json").write_text(json.dumps(expected) + "\n")
        conversation = Conversation(id="c_probe_" + uuid4().hex, title=probe.id)
        repository.create_conversation(conversation)
        mission = Mission(id="m_probe_" + uuid4().hex, title="A2A " + probe.id, goal=goal_for(probe, receipt),
                          conversation_id=conversation.id, orchestration=OrchestrationConfig(
                              mode=OrchestrationMode.DYNAMIC, participant_ids=["chulan", "baobao"]))
        repository.create_mission(mission)
        repository.add_message(Message(id="msg_" + uuid4().hex, mission_id=mission.id, role="user",
                                       content=mission.goal, conversation_id=conversation.id))
        mapping = {"chulan": probe.lead_model, "baobao": probe.peer_model}
        registry = AgentRegistry([AgentProfile(id=agent_id, display_name=agent_id, role="中性任务参与者",
            persona="基于可见事实完成任务，不虚构证据。", default_provider_id=manifest["models"][key]["provider"],
            fallback_provider_ids=[]) for agent_id, key in mapping.items()])
        adapter = ProbeAdapter(SimpleNamespace(settings=settings), ledger, {"id": probe.id, "mapping": mapping},
                               probe, trial_dir, receipt, factory=factory)
        print(f"[{probe.id}] starting", flush=True)
        ProbeRunner(repository, settings, adapter, agent_registry=registry).run(mission.id)
        result = assess(repository, mission, probe, trial_dir, expected, adapter.host_calls)
        manifest["results"].append(result)
        manifest["observed_api_equivalent_usd"] = float(ledger.spent)
        manifest["observed_host_calls"] = len(ledger.calls)
        print(f"  passed={result['passed']} requests={result['a2a_requests']} roundtrip={result['roundtrip_verified']} cost=${ledger.spent:.6f}", flush=True)
        save()
        if ledger.stop_reason or (probe.kind == "protocol" and not result["passed"]):
            manifest["status"] = "stopped"
            manifest["stop_reason"] = ledger.stop_reason or "Required protocol probe failed; do not proceed to autonomous probes"
            break
    else:
        manifest["status"] = "completed"
    manifest["passed_probes"] = sum(row["passed"] for row in manifest["results"])
    save()
    lines = ["# 真实 A2A 求助探针", "", f"状态：{manifest['status']}；通过 {manifest['passed_probes']}/{len(manifest['results'])}。", "",
             "| 探针 | 通过 | 请求 | 完整往返 | 最终正确 | 宿主调用 |", "|---|---|---:|---|---|---:|"]
    for row in manifest["results"]:
        lines.append(f"| {row['id']} | {row['passed']} | {row['a2a_requests']} | {row['roundtrip_verified']} | {row['final_correct']} | {row['host_calls']} |")
    lines += ["", f"未计缓存折扣的 API 等价估算：${ledger.spent:.6f}；实际 CLI 账单未知。", "",
              "protocol 明确要求调用；autonomous 只提供搭档持有缺失事实这一条件；direct 信息齐全。一次样本不能证明稳定自主调度能力，也不构成协作提高编码能力的证据。"]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return manifest
