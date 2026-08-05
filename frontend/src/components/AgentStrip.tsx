import { getPersonaColor } from "../lib/personaColors";
import { compactPersonaProviderLabel } from "../lib/providerLabels";
import type { AgentParticipant } from "../types";

export function AgentStrip({ participants }: { participants: AgentParticipant[] }) {
  return (
    <section className="agent-strip" aria-label="Agent 参与状态">
      {participants.map((participant) => {
        const color = getPersonaColor(participant.agentId);
        const identity = participant.participated
          ? compactPersonaProviderLabel(participant.personaLabel, participant.providerLabel)
          : participant.personaLabel;
        const cardClass = [
          "agent-strip-card",
          `agent-strip-card-${participant.agentId}`,
          participant.participated ? "" : "agent-strip-card-inactive",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <article key={participant.agentId} className={cardClass}>
            <span
              className="agent-strip-avatar"
              aria-hidden="true"
              style={{ borderColor: participant.participated ? color.border : undefined }}
            >
              {color.avatar}
            </span>
            <div className="agent-strip-copy">
              <strong style={{ color: participant.participated ? color.name : undefined }}>
                {identity}
              </strong>
              {!participant.participated ? <span>未参与</span> : null}
            </div>
          </article>
        );
      })}
    </section>
  );
}
