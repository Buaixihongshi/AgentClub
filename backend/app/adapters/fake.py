from typing import Optional

from backend.app.adapters.base import CLIAdapter, TurnOutcome
from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPTools


class FakeCLIAdapter(CLIAdapter):
    def __init__(self, response: Optional[str] = None):
        self.response = response

    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        from backend.app.orchestration.deliberation import is_v2
        if is_v2(tools.repository.get_mission(mission_id)):
            return self._discussion_turn(mission_id, agent_id, turn_id, provider_id, tools)
        content = self.response or "[演示:{agent_id}] {prompt}".format(
            agent_id=agent_id,
            prompt=prompt,
        )
        tools.log_thought(
            mission_id,
            agent_id,
            turn_id,
            "我正在用演示适配器模拟思考：{agent_id} 正在处理这个任务。".format(agent_id=agent_id),
            provider_id,
            kind="reasoning",
            tags=["fake", "smoke-test"],
        )
        tools.publish_message(mission_id, agent_id, turn_id, content, provider_id)

        mission = tools.repository.get_mission(mission_id)
        if mission is not None and mission.room_mode != "roundtable" and _requires_demo_artifact(mission.goal):
            tools.write_artifact(
                mission_id,
                agent_id,
                turn_id,
                "demo-{agent_id}.md".format(agent_id=agent_id),
                "# 演示产物：{agent_id}\n\n{content}\n".format(agent_id=agent_id, content=content),
            )

        return TurnOutcome(status=TurnStatus.COMPLETED)

    def _discussion_turn(self, mission_id, agent_id, turn_id, provider_id, tools):
        from backend.app.domain.deliberation import SECTION_IDS
        view = tools.read_context(mission_id, agent_id=agent_id, turn_id=turn_id, provider_id=provider_id)
        for source_id in view.get('required_read_ids', []):
            offset = 0
            while True:
                page = tools.read_discussion_result(mission_id, agent_id, turn_id, provider_id, source_id, offset)
                if page['next_offset'] is None:
                    break
                offset = page['next_offset']
        phase = view['phase']
        if phase == 'independent':
            result = {'kind': 'opinion', 'position_summary': f'[演示:{agent_id}] 先明确约束，再验证方案。',
                      'claims': [{'key': 'main', 'decision_key': 'next_step', 'statement': '需要以证据验证方案价值。',
                                  'recommendation': '先验证一个真实场景', 'evidence_status': 'unverified'}]}
        elif phase == 'exchange':
            result = {'kind': 'contribution', 'no_addition': True}
        elif phase == 'review':
            result = {'kind': 'review', 'draft_id': view['draft_id'], 'no_findings': True}
        else:
            refs = [c['id'] for r in view['results'] if r['kind'] == 'opinion' for c in r['data']['claims']]
            result = {'kind': 'draft', 'sections': [{'id': sid, 'content': '演示综合：建议先验证真实场景，当前意见一致仍未经过事实验证。',
                                                   'source_ids': refs} for sid in SECTION_IDS],
                      'issue_decisions': [{'issue_id': key, 'status': 'resolved' if issue['status'] == 'resolved' else 'unresolved',
                                           'decision': '演示综合：按现有证据保留结论并验证。'}
                                          for key, issue in view['issues'].items() if issue['category'] != 'needs_human']}
        tools.submit_discussion_result(mission_id, agent_id, turn_id, provider_id, result)
        return TurnOutcome(status=TurnStatus.COMPLETED)


def _requires_demo_artifact(goal: str) -> bool:
    """Keep offline demo behavior aligned with explicit delivery intent.

    Real providers receive the full delivery policy in their prompt. The fake
    adapter has no model to interpret that policy, so it uses a deliberately
    small set of explicit execution/delivery phrases instead of treating every
    agent turn as a file request.
    """
    normalized = goal.strip().lower()
    markers = (
        "实现一个",
        "实现这个",
        "执行改动",
        "帮我实现",
        "请实现",
        "修复",
        "写一个",
        "写个",
        "写份",
        "生成一个",
        "创建一个",
        "整理成",
        "输出为",
        "交付",
        "implement ",
        "fix ",
        "build ",
        "create ",
        "write ",
        "generate ",
        "report",
        "script",
        "readme",
    )
    return any(marker in normalized for marker in markers)
