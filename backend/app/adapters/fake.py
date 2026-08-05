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

        if agent_id == "baobao":
            tools.write_artifact(
                mission_id,
                agent_id,
                turn_id,
                "demo-baobao.md",
                "# 演示产物：冯宝宝\n\n{content}\n".format(content=content),
            )

        return TurnOutcome(status=TurnStatus.COMPLETED)
