import { useState } from "react";
import type { DiscussionControlAction, DiscussionControlFields, DiscussionView, MissionStatus } from "../types";
import { participantLabel } from "../lib/roundtableView";

const STAGES = [
  ["independent", "独立思考"], ["exchange", "分歧讨论"],
  ["awaiting_synthesizer", "选择综合者"], ["synthesis", "扇入综合"],
  ["review", "原参与者复核"], ["finalizing", "最终收束"],
] as const;
const PROGRESS: Record<string, string> = {
  reserved: "排队中", running: "正在处理", submitted: "已提交",
  failed: "未完成", interrupted: "运行中断", cancelled: "已取消",
};
const ISSUES: Record<string, string> = {
  open: "待核验", resolved: "已处理并记录依据", unresolved: "保留分歧",
  needs_information: "缺少信息", needs_human: "需要你决定",
};
const DECISION_STATUS: Record<string, string> = {
  resolved: "已决定", unresolved: "保留未解", deferred: "暂缓决定",
};
const SECTIONS: Record<string, string> = {
  recommendation: "建议结论", consensus: "共识与验证状态", disagreements: "保留的分歧",
  pending: "待决事项", actions: "建议行动", sources: "来源",
};

export function DeliberationStatus(props: {
  discussion: DiscussionView;
  missionStatus: MissionStatus;
  busy?: boolean;
  onContinue: () => void;
  onControl?: (action: DiscussionControlAction, fields?: DiscussionControlFields) => void;
}) {
  const { discussion: d } = props;
  const [selected, setSelected] = useState("");
  const [goal, setGoal] = useState("");
  const disabled = Boolean(props.busy) || !props.onControl;
  const ended = d.phase === "ended";
  const waiting = props.missionStatus === "awaiting_input";
  const blocked = props.missionStatus === "blocked";
  const results = d.results ?? [];
  const sourceLabel = (id: string) => {
    const owner = results.find((result) => result.id === id || result.data.claims?.some((claim) => claim.id === id));
    if (owner) return `${participantLabel(owner.agent_id)} · ${owner.kind === 'opinion' ? '初始判断' : owner.kind === 'review' ? '复核记录' : '原始发言'}`;
    const issue = d.issues?.find((item) => item.id === id);
    if (issue) return `分歧：${issue.problem}`;
    return id.startsWith('artifact:') ? `材料：${id.split(':').slice(2).join(':')}` : '冻结对话材料';
  };
  const final = results.find((r) => r.id === (d.final_id || d.draft_id));
  const failed = d.progress?.find((p) => p.status === "failed" || p.status === "interrupted");
  const failures = d.progress?.filter((p) => p.status === "failed" || p.status === "interrupted") ?? [];
  const current = d.progress?.filter((p) => p.phase === d.phase) ?? [];
  const control = (action: DiscussionControlAction, fields?: DiscussionControlFields) => props.onControl?.(action, fields);

  return <section className="discussion-status deliberation-status" aria-label="六阶段讨论">
    <div className="discussion-status-head"><h2>独立判断与讨论</h2>
      <span role="status">{d.pending_control === "stop" ? "正在停止" : ended ? (d.end_reason === "user_stop" ? "已停止" : "已收束") : blocked ? "本阶段未完成" : waiting ? "等待你的选择" : STAGES.find(([id]) => id === d.phase)?.[1]}</span>
    </div>
    <ol className="deliberation-stages" aria-label="讨论流程">
      {STAGES.map(([id, label], index) => <li key={id} aria-current={!ended && d.phase === id ? "step" : undefined}>
        <span>{index + 1}</span>{label}
      </li>)}
    </ol>
    {!waiting && !ended && !blocked && <div className="deliberation-progress">
      {current.map((p) => <span key={p.turn_id}>{participantLabel(p.agent_id)} · {PROGRESS[p.status] ?? p.status}</span>)}
      {d.phase === "independent" && <p>各自基于同一份材料判断，全部提交后统一展示。</p>}
      {d.phase === "exchange" && <p>正在核验分歧 · 第 {d.round_index} / 最多 {d.max_exchange_rounds} 轮
        {!!d.exchange_agents?.length && <> · 本轮参与者：{d.exchange_agents.map((agent) => participantLabel(agent)).join("、")}</>}</p>}
    </div>}
    {d.agreement?.skip && <p className="deliberation-agreement" role="status">
      观点在决策项、关键假设与建议行动上基本一致，已跳过分歧讨论。这不代表事实已经验证。
    </p>}

    {results.some((r) => r.kind === "opinion") && <div className="deliberation-opinions">
      {results.filter((r) => r.kind === "opinion").map((r) => <article key={r.id}>
        <h3>{participantLabel(r.agent_id)} · 初始判断</h3>
        <p>{r.data.position_summary}</p>
        <details><summary>查看原始主张与依据状态</summary>
          <ul>{r.data.claims?.map((c) => <li key={c.id}>{c.statement} <small>{c.evidence_status === "verified" ? "附有验证来源" : "仍需核验"}</small></li>)}</ul>
        </details>
      </article>)}
    </div>}

    {!!d.issues?.length && <div className="deliberation-issues"><h3>分歧与待验证事项</h3>
      {d.issues.map((issue) => <article key={issue.id}><strong>{issue.problem}</strong><p>{issue.impact}</p>
        <small>{ISSUES[issue.status] ?? issue.status}</small>
        {issue.technical_decision && <p className="deliberation-issue-decision">
          技术处置（{DECISION_STATUS[issue.decision_status ?? ""] ?? issue.decision_status}）：{issue.technical_decision}
        </p>}
      </article>)}
    </div>}

    {waiting && d.awaiting_reason === "select_synthesizer" && <div className="deliberation-human-action">
      <h3>请选择综合者</h3><p>由其整理观点与分歧，之后由其他原参与者复核。系统不会默认替你选择。</p>
      <label>负责综合的参与者 <select value={selected} onChange={(event) => setSelected(event.target.value)} disabled={disabled}>
        <option value="">请选择</option>
        {d.participant_ids.map((id) => <option key={id} value={id}>{participantLabel(id)}</option>)}
      </select></label>
      <button type="button" disabled={disabled || !selected} onClick={() => control("select_synthesizer", { agent_id: selected })}>由其综合</button>
    </div>}

    {final && <div className="deliberation-draft"><h3>{d.final_id ? "最终结果" : "综合稿 · 待复核"} · {participantLabel(d.synthesizer_id)}</h3>
      {final.data.sections?.map((section) => <details key={section.id} open={section.id === "recommendation" || section.id === "disagreements"}>
        <summary>{SECTIONS[section.id] ?? section.id}</summary><p className="deliberation-section-text">{section.content}</p>
        {!!section.source_ids.length && <details className="deliberation-sources"><summary>查看依据（{section.source_ids.length}）</summary>
          <ul>{section.source_ids.map((id) => <li key={id} title={id}>{sourceLabel(id)}</li>)}</ul>
        </details>}
      </details>)}
      {d.final_id && d.final_id !== d.draft_id && <p role="note">此稿已按复核意见修订，修订部分未再次独立复核。</p>}
      {!!final.data.dispositions?.length && <details><summary>逐条修订记录</summary><ul>
        {final.data.dispositions.map((item) => <li key={item.finding_id}>{item.action === "accepted" ? "已采纳" : item.action === "partially_accepted" ? "部分采纳" : "未采纳"}：{item.reason}</li>)}
      </ul></details>}
      {!!final.data.issue_decisions?.length && <details><summary>技术问题处置（{final.data.issue_decisions.length}）</summary><ul>
        {final.data.issue_decisions.map((item) => {
          const issue = d.issues?.find((candidate) => candidate.id === item.issue_id);
          return <li key={item.issue_id}>
            <strong>{issue?.problem ?? item.issue_id}</strong>
            <span> · {DECISION_STATUS[item.status] ?? item.status}：{item.decision}</span>
          </li>;
        })}
      </ul></details>}
    </div>}

    {!!results.filter((r) => r.kind === "review").length && <div className="deliberation-reviews"><h3>原参与者复核</h3>
      {results.filter((r) => r.kind === "review").map((r) => <article key={r.id}><strong>{participantLabel(r.agent_id)}</strong>
        {r.data.no_findings ? <p>本次复核未发现遗漏或误读。</p> : <ul>{r.data.findings?.map((f) => <li key={f.id}>{f.blocking ? "需优先处理：" : ""}{f.problem}<p>期望修正：{f.desired_change}</p></li>)}</ul>}
      </article>)}
    </div>}

    {waiting && d.awaiting_reason === "resolve_value_choice" && <div className="deliberation-human-action"><h3>这些取舍需要你决定</h3>
      {d.value_decisions?.filter((decision) => !d.decisions?.[decision.id]).map((decision) => <fieldset key={decision.id} disabled={disabled}>
        <legend>{decision.question}</legend>
        {decision.options.map((option) => <button key={option.id} type="button" onClick={() => control("record_decision", { decision_id: decision.id, option_id: option.id })}>{option.label}</button>)}
        <button type="button" onClick={() => control("record_decision", { decision_id: decision.id, option_id: "defer" })}>明确留待以后决定</button>
      </fieldset>)}
    </div>}

    {waiting && d.awaiting_reason === "revise_input" && <div className="deliberation-human-action"><h3>有新的补充条件</h3>
      <p>为让参与者基于相同材料判断，请选择保留原条件继续，或带完整新议题重新开始。</p>
      <label>完整的新议题<textarea value={goal} onChange={(event) => setGoal(event.target.value)} disabled={disabled} /></label>
      <button type="button" disabled={disabled || !goal.trim()} onClick={() => control("restart_with_input", { input_ids: d.pending_inputs, goal: goal.trim() })}>以新条件重新讨论</button>
      <button type="button" disabled={disabled} onClick={() => control("defer_input", { input_ids: d.pending_inputs })}>补充留待下次，继续原议题</button>
    </div>}

    {!!Object.keys(d.decisions ?? {}).length && <div><h3>你的决定</h3><ul>
      {d.value_decisions?.filter((item) => d.decisions?.[item.id]).map((item) => <li key={item.id}>
        {item.question}：{d.decisions?.[item.id] === "defer" ? "明确延后" : item.options.find((option) => option.id === d.decisions?.[item.id])?.label}
      </li>)}
    </ul></div>}

    {blocked && <div className="deliberation-human-action" role="alert"><p>本阶段尚未完成。有效提交已保留，未提前解封。</p>
      {!!failures.length && <details><summary>查看运行错误</summary><ul>{failures.map((p) => <li key={p.turn_id}>
        <strong>{participantLabel(p.agent_id)}{p.provider_id ? ` / ${p.provider_id}` : ''}</strong>：{p.error || '本回合运行中断。'}
        <small> · 已尝试 {p.attempt} / 2 次{p.exit_code != null ? ` · 退出码 ${p.exit_code}` : ''}</small>
      </li>)}</ul></details>}
      {d.retryable && failed && <button type="button" disabled={disabled} onClick={() => control("retry", { turn_id: failed.turn_id })}>重试失败批次</button>}
      {!d.retryable && <p>重试额度已用尽，请停止后新建讨论。</p>}
    </div>}

    <div className="discussion-controls">
      {!ended && <>
        {!waiting && !blocked && ["independent", "exchange"].includes(d.phase) && <button type="button" disabled={disabled || Boolean(d.pending_control)} onClick={() => control("finish")}>本批次后选择综合者</button>}
        <button type="button" disabled={disabled} onClick={() => control("stop")}>停止讨论</button>
      </>}
      {ended && <button type="button" disabled={Boolean(props.busy)} onClick={props.onContinue}>继续讨论</button>}
      {ended && d.final_id && <button type="button" disabled={disabled || Boolean(d.knowledge_candidate_id)} onClick={() => control("save_knowledge_candidate")}>
        {d.knowledge_candidate_id ? "已保存知识候选" : "保存为知识候选"}
      </button>}
    </div>
    <details className="deliberation-runtime"><summary>运行详情</summary>
      <p>已用 {d.turns_used} / 最多 {d.turns_limit} 次阶段调用 · 规则 {d.rules_version}</p>
      {d.attempts_used != null && <p>实际运行尝试 {d.attempts_used} 次 · 每个阶段回合最多尝试 2 次</p>}
      <p>协议：{d.protocol} · 状态版本：{d.revision}</p>
      <p>构建：{d.build_id} · 规则指纹：{d.rules_hash?.slice(0, 16)}</p>
    </details>
  </section>;
}
