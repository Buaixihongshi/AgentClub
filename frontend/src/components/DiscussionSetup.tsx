import { getPersonaColor } from "../lib/personaColors";
import {
  defaultDiscussionTurnLimit,
  discussionParticipantOptions,
} from "../lib/roundtableView";
import type { RoomMode } from "../types";

/**
 * Mode switch plus participant picker for starting a new mission. Roundtable
 * selection is ordered (opening order) and refuses to lock in fewer than two
 * partners; nothing here assigns permanent roles to a persona.
 */
export function DiscussionSetup(props: {
  mode: RoomMode;
  participantIds: string[];
  onModeChange: (mode: RoomMode) => void;
  onToggleParticipant: (agentId: string) => void;
  disabled?: boolean;
  /** A running mission locks the mode and the participant set. */
  locked?: boolean;
  hint?: string | null;
}) {
  const options = discussionParticipantOptions();
  const roundtable = props.mode === "roundtable";
  const locked = Boolean(props.locked);
  return (
    <section className="discussion-setup" aria-label="对话模式">
      <div className="discussion-mode-switch" role="radiogroup" aria-label="对话模式">
        <button
          type="button"
          role="radio"
          aria-checked={!roundtable}
          className={`discussion-mode-option ${!roundtable ? "is-active" : ""}`}
          disabled={props.disabled || locked}
          onClick={() => props.onModeChange("task")}
        >
          任务
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={roundtable}
          className={`discussion-mode-option ${roundtable ? "is-active" : ""}`}
          disabled={props.disabled || locked}
          onClick={() => props.onModeChange("roundtable")}
        >
          多人讨论
        </button>
      </div>

      {roundtable ? (
        <div className="discussion-participants">
          <p className="discussion-participants-hint">
            选 2～3 位伙伴，按选择顺序开场。角色只有性格差别，没有固定岗位。
          </p>
          <ul className="discussion-participant-list" aria-label="参与者">
            {options.map((option) => {
              const checked = props.participantIds.includes(option.agentId);
              const color = getPersonaColor(option.agentId);
              return (
                <li key={option.agentId}>
                  <label
                    className={`discussion-participant-card ${checked ? "is-selected" : ""}`}
                    style={{ borderColor: checked ? color.border : undefined }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={props.disabled || locked}
                      onChange={() => props.onToggleParticipant(option.agentId)}
                    />
                    <span className="discussion-participant-avatar" aria-hidden="true">
                      {color.avatar}
                    </span>
                    <span className="discussion-participant-copy">
                      <strong style={{ color: checked ? color.name : undefined }}>
                        {option.label}
                      </strong>
                      <span className="discussion-participant-role">{option.role}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {locked ? (
            <p className="discussion-lock-note" role="status">
              讨论进行中：模式和参与者已锁定，结束后点“继续讨论”可以调整。
            </p>
          ) : (
            <p className="discussion-budget-hint">
              最后一格留给总结：默认最多 {defaultDiscussionTurnLimit(props.participantIds.length)} 个回合（含总结）。
            </p>
          )}
        </div>
      ) : null}

      {props.hint && !locked ? (
        <p role="alert" className="discussion-hint">
          {props.hint}
        </p>
      ) : null}
    </section>
  );
}
