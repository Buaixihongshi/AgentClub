from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from backend.app.domain.models import TurnStatus
from backend.app.mcp.tools import MCPTools


@dataclass(frozen=True)
class TurnOutcome:
    status: TurnStatus
    summary: Optional[str] = None
    session_id: Optional[str] = None


class CLIAdapter(ABC):
    @abstractmethod
    def run_turn(
        self,
        mission_id: str,
        agent_id: str,
        turn_id: str,
        provider_id: str,
        prompt: str,
        tools: MCPTools,
    ) -> TurnOutcome:
        """Run one agent turn against a CLI provider."""
