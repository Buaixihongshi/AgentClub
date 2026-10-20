import { formatMissionStatus } from "../lib/missionView";
import {
  formatDiscussionBudget,
  formatDiscussionControl,
  formatDiscussionEndReason,
  formatDiscussionPhase,
  participantLabel,
} from "../lib/roundtableView";
import type { DiscussionView, MissionStatus } from "../types";
import { DeliberationStatus } from "./DeliberationStatus";

/**
 * Roundtable run state. Every displayed value comes from the mission detail
 * snapshot (API/SSE); nothing is inferred from local storage or timers.
 */
export function DiscussionStatus(props: {
  discussion: DiscussionView;
  missionStatus: MissionStatus;
  busy?: boolean;
  onRequestFinish: () => void;
  onRequestStop: () => void;
  onContinue: () => void;
  onControl?: (action: import("../types").DiscussionControlAction, fields?: import("../types").DiscussionControlFields) => void;
}) {
  const { discussion } = props;
  if (discussion.protocol === "deliberation_v2") return <DeliberationStatus {...props} />;
  const active = discussion.phase !== "ended";
  const participantIds = discussion.participant_ids ?? [];
  const controlsDisabled = Boolean(props.busy) || Boolean(discussion.pending_control);
  const pendingLabel = formatDiscussionControl(discussion.pending_control);
  const unanswered = (discussion.unanswered_interrupt_ids ?? []).length;
  const summaryCount = (discussion.summary_message_ids ?? []).length;

  return (
    <section className="discussion-status" aria-label="多人讨论">
      <div className="discussion-status-overview">
        <div className="discussion-status-head">
          <h2>多人讨论</h2>
          <span className={`discussion-phase discussion-phase-${discussion.phase}`}>
            {formatDiscussionPhase(discussion.phase)}
          </span>
        </div>
        <div className="discussion-status-meta">
          <span className="discussion-budget">
            {formatDiscussionBudget(discussion.turns_used, discussion.turns_limit)}
          </span>
          <span className="discussion-participants-line">
            参与者：{participantIds.map((id) => participantLabel(id)).join("、")}
          </span>
          {active ? (
            <span className="discussion-speaker">
              当前发言：{discussion.current_speaker ? participantLabel(discussion.current_speaker) : "等待领取回合"}
            </span>
          ) : (
            <span className="discussion-end-reason">
              结束原因：{formatDiscussionEndReason(discussion.end_reason) || "未记录"}
              {summaryCount > 0 ? ` · 总结消息 ${summaryCount} 条` : ""}
            </span>
          )}
          <span className="discussion-mission-status">任务状态：{formatMissionStatus(props.missionStatus)}</span>
        </div>
      </div>

      {pendingLabel ? (
        <p role="status" className="discussion-pending">
          {pendingLabel}
        </p>
      ) : null}

      {unanswered > 0 ? (
        <p className="discussion-deferred">
          有 {unanswered} 条点名没能单独回应，会在总结里处理。
        </p>
      ) : null}

      {active ? (
        <div className="discussion-controls">
          <div className="discussion-control-buttons">
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={props.onRequestFinish}
            >
              本轮后收尾
            </button>
            <button
              type="button"
              className="discussion-stop-button"
              disabled={controlsDisabled}
              onClick={() => props.onRequestStop()}
            >
              本轮后停止
            </button>
          </div>
        </div>
      ) : (
        <div className="discussion-controls">
          <button
            type="button"
            className="discussion-continue-button"
            disabled={Boolean(props.busy)}
            onClick={() => props.onContinue()}
          >
            继续讨论
          </button>
        </div>
      )}
    </section>
  );
}
