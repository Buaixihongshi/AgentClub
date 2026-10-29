import { useState } from "react";
import { participantLabel } from "../lib/roundtableView";
import type { DiscussionControlAction, DiscussionControlFields, DiscussionView, DivergenceItem, MissionStatus } from "../types";

const PROGRESS: Record<string, string> = {
  reserved: "等待开始", running: "正在思考", submitted: "已完成", failed: "未完成",
  interrupted: "运行中断", cancelled: "已取消",
};
const STEP: Record<string, string> = {
  independent: "各自独立思考 · 全部完成后一起公开", exchange: "查证分歧中", synthesis: "正在综合",
};
const ISSUE_STATUS: Record<string, string> = {
  open: "待核对", resolved: "已处理", unresolved: "仍有分歧", needs_information: "缺少信息", needs_human: "需要你决定",
};

function Positions({ item }: { item: DivergenceItem }) {
  return <>{item.positions.map((p, index) => <span key={p.claim_id}>
    {index > 0 && " / "}{participantLabel(p.agent_id)} {p.recommendation}
  </span>)}</>;
}

/**
 * Quick discussions end the independent step here: what the voices agree on,
 * where they split, and the user's next step. Comparison is by decision key on
 * the host, so differently worded disagreements can be missed; the card says so.
 */
export function DivergenceCard(props: {
  discussion: DiscussionView;
  missionStatus: MissionStatus;
  busy?: boolean;
  onControl?: (action: DiscussionControlAction, fields?: DiscussionControlFields) => void;
}) {
  const { discussion: d } = props;
  const [synthesizer, setSynthesizer] = useState(d.participant_ids[0] ?? "");
  const [deciding, setDeciding] = useState(false);
  const [decision, setDecision] = useState("");
  const [decisionError, setDecisionError] = useState("");
  if (d.protocol !== "deliberation_v2" || d.depth !== "quick" || d.phase === "ended") return null;
  const choosing = props.missionStatus === "awaiting_input" && d.awaiting_reason === "choose_next";
  const choices = new Set(d.choices ?? []);
  const disabled = Boolean(props.busy) || !props.onControl;
  const control = (action: DiscussionControlAction, fields?: DiscussionControlFields) => props.onControl?.(action, fields);
  const current = new Map((d.progress ?? []).filter((turn) => turn.phase === d.phase).map((turn) => [turn.agent_id, turn]));
  const div = d.divergence;
  const issues = d.issues ?? [];
  const supplements = d.pending_inputs?.length ?? 0;

  if (!choosing) {
    if (!STEP[d.phase]) return null;
    return <section className="divergence-card is-running" aria-label="讨论进展">
      <p className="divergence-step">{STEP[d.phase]}</p>
      <ul className="divergence-progress">
        {[...current.values()].map((turn) => <li key={turn.turn_id}>
          <strong>{participantLabel(turn.agent_id)}</strong> {PROGRESS[turn.status] ?? turn.status}
        </li>)}
      </ul>
    </section>;
  }

  const submitDecision = () => {
    if (!decision.trim()) {
      setDecisionError("先写下你的决定");
      return;
    }
    control("user_decide", { text: decision.trim() });
  };

  return <section className="divergence-card" aria-label="分歧卡">
    <h3>分歧卡</h3>
    {!!div?.conflicts.length && <ul className="divergence-list">
      {div.conflicts.map((item) => <li key={item.decision_key}>
        <span className="divergence-tag is-conflict">分歧</span>{item.decision_key}：<Positions item={item} />
      </li>)}
    </ul>}
    {!!issues.length && <ul className="divergence-list">
      {issues.map((issue) => <li key={issue.id}>
        <span className="divergence-tag is-issue">{ISSUE_STATUS[issue.status] ?? issue.status}</span>{issue.problem}
      </li>)}
    </ul>}
    {!!div?.agreed.length && <ul className="divergence-list">
      {div.agreed.map((item) => <li key={item.decision_key}>
        <span className="divergence-tag is-agreed">一致</span>{item.decision_key}：{item.positions[0]?.recommendation}
      </li>)}
    </ul>}
    {!!div?.single.length && <details className="divergence-single"><summary>只有一方提到 · {div.single.length} 项</summary>
      <ul>{div.single.map((item) => <li key={item.decision_key}>{item.decision_key}：<Positions item={item} /></li>)}</ul>
    </details>}
    {!div?.conflicts.length && !issues.length && <p className="divergence-note">按判断项比对没有发现明确分歧，这不代表结论已经验证。</p>}
    <p className="divergence-note">
      按判断项自动比对，措辞不同的分歧可能漏掉 · 已用 {d.turns_used} 次调用
      {supplements > 0 && <> · 已收到 {supplements} 条补充，会带入下一步</>}
    </p>
    <div className="divergence-actions">
      {choices.has("verify_conflicts") && <button type="button" disabled={disabled} onClick={() => control("verify_conflicts")}>查证这个分歧</button>}
      {choices.has("synthesize") && <span className="divergence-synthesize">
        <select aria-label="综合者" value={synthesizer} disabled={disabled} onChange={(event) => setSynthesizer(event.target.value)}>
          {d.participant_ids.map((id) => <option key={id} value={id}>{participantLabel(id)}</option>)}
        </select>
        <button type="button" disabled={disabled} onClick={() => control("synthesize", { agent_id: synthesizer })}>让其综合</button>
      </span>}
      {choices.has("user_decide") && <button type="button" disabled={disabled} aria-expanded={deciding} onClick={() => setDeciding((open) => !open)}>我来定</button>}
      {choices.has("escalate") && <button type="button" disabled={disabled} onClick={() => control("escalate")}>正式评审</button>}
      {choices.has("finish") && <button type="button" className="discussion-skill-quiet" disabled={disabled} onClick={() => control("finish")}>就到这里</button>}
    </div>
    {deciding && <div className="divergence-decision">
      <label>你的决定<textarea rows={2} value={decision} disabled={disabled}
        onChange={(event) => { setDecision(event.target.value); setDecisionError(""); }} /></label>
      {decisionError && <p className="divergence-error" role="alert">{decisionError}</p>}
      <button type="button" disabled={disabled} onClick={submitDecision}>记录决定</button>
    </div>}
  </section>;
}
