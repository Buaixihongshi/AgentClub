import { useState } from "react";
import { formatMissionStatus } from "../lib/missionView";
import {
  formatDiscussionBudget,
  formatDiscussionControl,
  formatDiscussionEndReason,
  formatDiscussionPhase,
  participantLabel,
} from "../lib/roundtableView";
import type { DiscussionView, MissionStatus } from "../types";

/**
 * Roundtable run state. Every displayed value comes from the mission detail
 * snapshot (API/SSE); nothing is inferred from local storage or timers.
 */
export function DiscussionStatus(props: {
  discussion: DiscussionView;
  missionStatus: MissionStatus;
  busy?: boolean;
  onRequestFinish: (summaryAgentId?: string) => void;
  onRequestStop: () => void;
  onContinue: () => void;
}) {
  const { discussion } = props;
  const [summaryAgentId, setSummaryAgentId] = useState("");
  const active = discussion.phase !== "ended";
  const participantIds = discussion.participant_ids ?? [];
  const effectiveSummaryAgentId = participantIds.includes(summaryAgentId) ? summaryAgentId : "";
  const controlsDisabled = Boolean(props.busy) || Boolean(discussion.pending_control);
  const pendingLabel = formatDiscussionControl(discussion.pending_control);
  const unanswered = (discussion.unanswered_interrupt_ids ?? []).length;
  const summaryCount = (discussion.summary_message_ids ?? []).length;

  return (
    <section className="discussion-status" aria-label="多人讨论">
      <div className="discussion-status-head">
        <h2>多人讨论</h2>
        <span className={`discussion-phase discussion-phase-${discussion.phase}`}>
          {formatDiscussionPhase(discussion.phase)}
        </span>
      </div>

      <p className="discussion-budget">
        {formatDiscussionBudget(discussion.turns_used, discussion.turns_limit)}
      </p>
      <p className="discussion-participants-line">
        参与者：{participantIds.map((id) => participantLabel(id)).join("、")}
      </p>

      {active ? (
        <p className="discussion-speaker">
          当前发言：{discussion.current_speaker ? participantLabel(discussion.current_speaker) : "等待领取回合"}
        </p>
      ) : (
        <p className="discussion-end-reason">
          结束原因：{formatDiscussionEndReason(discussion.end_reason) || "未记录"}
          {summaryCount > 0 ? ` · 总结消息 ${summaryCount} 条` : ""}
        </p>
      )}

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

      <p className="discussion-mission-status">任务状态：{formatMissionStatus(props.missionStatus)}</p>

      {active ? (
        <div className="discussion-controls">
          <label htmlFor="discussion-summary-agent">收尾人（可选）</label>
          <select
            id="discussion-summary-agent"
            value={effectiveSummaryAgentId}
            disabled={controlsDisabled}
            onChange={(event) => setSummaryAgentId(event.target.value)}
          >
            <option value="">由系统按顺序决定</option>
            {participantIds.map((id) => (
              <option key={id} value={id}>
                {participantLabel(id)}
              </option>
            ))}
          </select>
          <div className="discussion-control-buttons">
            <button
              type="button"
              disabled={controlsDisabled}
              onClick={() => props.onRequestFinish(effectiveSummaryAgentId || undefined)}
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
