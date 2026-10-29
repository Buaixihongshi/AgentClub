import { useState } from "react";
import { participantLabel } from "../lib/roundtableView";
import type {
  DiscussionControlAction,
  DiscussionControlFields,
  DiscussionView,
  MissionStatus,
} from "../types";

const ACTIVE_STATUS: Record<string, string> = {
  independent: "团队正在形成独立判断…",
  exchange: "团队正在核对分歧与依据…",
  awaiting_synthesizer: "成员观点已就绪，等待选择综合者",
  synthesis: "正在整理各方观点与建议…",
  review: "团队正在复核综合建议…",
  finalizing: "正在按复核意见修订结果…",
  choosing: "观点已就绪，等你选择下一步",
};
const QUICK_ENDINGS: Record<string, string> = {
  synthesized_unreviewed: "已综合（未经复核）", user_decided: "已记录你的决定", opinions_only: "讨论已结束",
};

/** Run controls stay beside the composer; substantive discussion content lives in the timeline. */
export function DiscussionSkillStatus(props: {
  discussion: DiscussionView;
  missionStatus: MissionStatus;
  busy?: boolean;
  onRequestFinish: () => void;
  onRequestStop: () => void;
  onContinue: () => void;
  onControl?: (action: DiscussionControlAction, fields?: DiscussionControlFields) => void;
}) {
  const { discussion: d } = props;
  const [selected, setSelected] = useState("");
  const [goal, setGoal] = useState("");
  const v2 = d.protocol === "deliberation_v2";
  const ended = d.phase === "ended";
  const blocked = !ended && props.missionStatus === "blocked";
  const waiting = !ended && props.missionStatus === "awaiting_input";
  const pending = Boolean(d.pending_control);
  const disabled = Boolean(props.busy) || pending || (v2 && !props.onControl);
  const failures = d.progress?.filter((turn) => ["failed", "interrupted"].includes(turn.status)) ?? [];
  const stopped = d.end_reason === "user_stop";
  const failedEnding = ["provider_failed", "summary_failed", "runner_failed", "no_successful_turn", "service_restart"].includes(d.end_reason ?? "");
  const status = d.pending_control === "stop"
    ? "正在停止团队讨论…"
    : ended
      ? stopped ? "讨论已停止" : QUICK_ENDINGS[d.end_reason ?? ""] ?? (failedEnding ? "讨论未完成" : d.end_reason === "input_revised" ? "已按新条件重新开始讨论" : "团队讨论已完成")
      : blocked ? "团队讨论需要处理运行问题"
        : waiting ? (d.awaiting_reason === "choose_next" ? ACTIVE_STATUS.choosing : "团队需要你的选择")
          : d.pending_control === "finish" ? "团队正在整理讨论结果…" : ACTIVE_STATUS[d.phase] ?? "团队正在讨论这个问题…";
  const control = (action: DiscussionControlAction, fields?: DiscussionControlFields) => props.onControl?.(action, fields);
  const stop = () => v2 ? control("stop") : props.onRequestStop();

  return <section className={`discussion-skill-status${blocked ? " has-error" : ""}`} aria-label="讨论技能">
    <div className="discussion-skill-status-line">
      <span className={`discussion-skill-indicator${ended ? " is-ended" : ""}`} aria-hidden="true" />
      <p role="status">{status}</p>
      {!ended && <button type="button" className="discussion-skill-quiet" disabled={disabled} onClick={stop}>停止讨论</button>}
      {!v2 && !ended && <button type="button" className="discussion-skill-quiet" disabled={disabled} onClick={props.onRequestFinish}>整理结果</button>}
      {!v2 && ended && <button type="button" className="discussion-skill-quiet" disabled={Boolean(props.busy)} onClick={props.onContinue}>继续讨论</button>}
      {v2 && ended && d.final_id && <button type="button" className="discussion-skill-quiet" disabled={disabled || Boolean(d.knowledge_candidate_id)} onClick={() => control("save_knowledge_candidate")}>
        {d.knowledge_candidate_id ? "已保存知识候选" : "保存为知识候选"}
      </button>}
    </div>

    {v2 && d.agreement?.skip && <p className="discussion-skill-note" role="status">
      观点在决策项、关键假设与建议行动上基本一致，已跳过分歧讨论；这不代表事实已验证。
    </p>}

    {v2 && waiting && d.awaiting_reason === "select_synthesizer" && <div className="discussion-skill-action">
      <p>请选择一位伙伴整理观点和分歧，其他伙伴会再复核。</p>
      <div className="discussion-skill-fields">
        <label>负责综合的伙伴<select value={selected} disabled={disabled} onChange={(event) => setSelected(event.target.value)}>
          <option value="">请选择</option>
          {d.participant_ids.map((id) => <option key={id} value={id}>{participantLabel(id)}</option>)}
        </select></label>
        <button type="button" disabled={disabled || !d.participant_ids.includes(selected)} onClick={() => control("select_synthesizer", { agent_id: selected })}>由其综合</button>
      </div>
    </div>}

    {v2 && waiting && d.awaiting_reason === "resolve_value_choice" && <div className="discussion-skill-action">
      {d.value_decisions?.filter((decision) => !d.decisions?.[decision.id]).map((decision) => <fieldset key={decision.id} disabled={disabled}>
        <legend>{decision.question}</legend>
        <div className="discussion-skill-buttons">
          {decision.options.map((option) => <button type="button" key={option.id} onClick={() => control("record_decision", { decision_id: decision.id, option_id: option.id })}>{option.label}</button>)}
          <button type="button" className="discussion-skill-quiet" onClick={() => control("record_decision", { decision_id: decision.id, option_id: "defer" })}>明确留待以后决定</button>
        </div>
      </fieldset>)}
    </div>}

    {v2 && waiting && d.awaiting_reason === "revise_input" && <div className="discussion-skill-action">
      <p>收到补充条件。可保留原议题继续，或让所有伙伴基于完整新条件重新判断。</p>
      <label>完整的新议题<textarea value={goal} disabled={disabled} onChange={(event) => setGoal(event.target.value)} rows={3} /></label>
      <div className="discussion-skill-buttons">
        <button type="button" disabled={disabled || !goal.trim()} onClick={() => control("restart_with_input", { input_ids: d.pending_inputs, goal: goal.trim() })}>以新条件重新讨论</button>
        <button type="button" className="discussion-skill-quiet" disabled={disabled} onClick={() => control("defer_input", { input_ids: d.pending_inputs })}>补充留待下次，继续原议题</button>
      </div>
    </div>}

    {blocked && <div className="discussion-skill-action" role="alert">
      <p>讨论暂未完成，已提交的判断会保留。</p>
      {!!failures.length && <details className="discussion-skill-errors">
        <summary>查看运行错误</summary>
        <ul>{failures.map((turn) => <li key={turn.turn_id}>
          <strong>{participantLabel(turn.agent_id)}{turn.provider_id ? ` / ${turn.provider_id}` : ""}</strong>：{turn.error || "本回合运行中断。"}
          <small>已尝试 {turn.attempt} / 2 次{turn.exit_code != null ? ` · 退出码 ${turn.exit_code}` : ""}</small>
        </li>)}</ul>
      </details>}
      {!failures.length && d.error && <p>{d.error}</p>}
      {v2 && d.retryable && failures[0] && <button type="button" disabled={disabled} onClick={() => control("retry", { turn_id: failures[0].turn_id })}>重试失败批次</button>}
      {v2 && !d.retryable && <p>重试额度已用尽，请停止后重新调用讨论技能。</p>}
    </div>}
  </section>;
}
