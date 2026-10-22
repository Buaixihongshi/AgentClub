import type { DiscussionView } from "../types";
import { participantLabel } from "../lib/roundtableView";

const PHASES: Record<string, string> = {
  independent: "独立判断", exchange: "核对分歧", awaiting_synthesizer: "等待选择综合者",
  synthesis: "整理综合建议", review: "复核综合建议", finalizing: "修订与收束", ended: "讨论已结束",
};
const PROGRESS: Record<string, string> = {
  reserved: "等待开始", running: "正在核对", submitted: "已提交", failed: "未完成",
  interrupted: "运行中断", cancelled: "已取消",
};
const ISSUE_STATUS: Record<string, string> = {
  open: "待核对", resolved: "已处理", unresolved: "保留分歧", needs_information: "待补充信息", needs_human: "待你决定",
};
const EVIDENCE: Record<string, string> = {
  verified: "附有验证来源", unverified: "待验证", inference: "推断", preference: "取舍偏好",
};
const SECTIONS: Record<string, string> = {
  recommendation: "建议结论", consensus: "共识", disagreements: "保留分歧", pending: "待决事项", actions: "下一步行动", sources: "来源",
};

function Excerpt({ text, limit = 200 }: { text: string; limit?: number }) {
  if (text.length <= limit) return <p className="discussion-highlight-text">{text}</p>;
  return <>
    <p className="discussion-highlight-text">{text.slice(0, limit)}…</p>
    <details><summary>展开完整内容</summary><p className="discussion-highlight-text">{text}</p></details>
  </>;
}

/** Only renders submitted content already released by the server, never private reasoning. */
export function DiscussionHighlights({ discussion: d, goal }: { discussion: DiscussionView; goal: string }) {
  if (d.protocol !== "deliberation_v2") return null;
  const results = d.results ?? [];
  const opinions = results.filter((result) => result.kind === "opinion");
  const reviews = results.filter((result) => result.kind === "review");
  const draft = results.find((result) => result.id === (d.final_id || d.draft_id));
  const recommendation = draft?.data.sections?.find((section) => section.id === "recommendation");
  const issues = [...(d.issues ?? [])].sort((a, b) => Number(a.status === "resolved") - Number(b.status === "resolved"));
  const ended = d.phase === "ended";
  // Progress includes past exchange rounds; only the latest turn for each member describes this round.
  const currentTurns = new Map((d.progress ?? []).filter((turn) => turn.phase === d.phase).map((turn) => [turn.agent_id, turn]));
  const activeIds = d.phase === "independent" ? d.participant_ids
    : d.phase === "exchange" ? (d.exchange_agents?.length ? d.exchange_agents : d.participant_ids)
      : [...new Set([...(d.current_speakers ?? []), ...currentTurns.keys()])];
  const content = <>
    <div className="discussion-highlights-heading">
      <h2>讨论重点</h2><span>{PHASES[d.phase] ?? "讨论进行中"}</span>
    </div>
    <p className="discussion-highlights-note">{ended ? "保留本轮观点、分歧与复核记录。" : "以下为阶段性讨论内容，最终结论以团队整理结果为准。"}</p>
    {!ended && !!activeIds.length && <ul className="discussion-member-progress" aria-label="本轮成员进展">
      {activeIds.map((id) => {
        const turn = currentTurns.get(id);
        const status = turn ? PROGRESS[turn.status] ?? "等待更新" : d.current_speakers?.includes(id) ? "正在核对" : "等待开始";
        return <li key={id}><strong>{participantLabel(id)}</strong><span>{status}</span></li>;
      })}
    </ul>}
    {!opinions.length && !draft && !issues.length && <div className="discussion-highlight-focus">
      <h3>本轮议题</h3><Excerpt text={goal} />
      <p className="discussion-highlights-note">{ended ? "本轮尚未形成可展示的讨论观点。" : "正在分别形成判断；本批次全部提交后，这里会展示具体观点和待核对的问题。"}</p>
    </div>}
    {!!issues.length && <div className="discussion-highlight-group">
      <h3>分歧与待解决问题 <small>{issues.length} 项</small></h3>
      {issues.slice(0, 3).map((issue) => <article key={issue.id} className="discussion-highlight-card">
        <span className="discussion-highlight-tag">{ISSUE_STATUS[issue.status] ?? "待核对"}</span>
        <Excerpt text={issue.problem} />
        <details><summary>查看影响与处理建议</summary>
          <p className="discussion-highlight-text">影响：{issue.impact}</p>
          {Object.entries(issue.positions ?? {}).map(([id, position]) => <p className="discussion-highlight-text" key={id}><strong>{participantLabel(id)}：</strong>{position.proposal}</p>)}
          {(issue.technical_decision || issue.resolution) && <p className="discussion-highlight-text">{issue.decision_status === "deferred" ? "暂缓决定" : issue.decision_status === "unresolved" ? "保留未解" : "处理记录"}：{issue.technical_decision || issue.resolution}</p>}
        </details>
      </article>)}
      {issues.length > 3 && <details><summary>其余 {issues.length - 3} 项问题</summary>
        {issues.slice(3).map((issue) => <article key={issue.id} className="discussion-highlight-card"><span className="discussion-highlight-tag">{ISSUE_STATUS[issue.status] ?? "待核对"}</span>
          <p className="discussion-highlight-text">{issue.problem}</p><p className="discussion-highlight-text">影响：{issue.impact}</p>
          {Object.entries(issue.positions ?? {}).map(([id, position]) => <p className="discussion-highlight-text" key={id}><strong>{participantLabel(id)}：</strong>{position.proposal}</p>)}
          {(issue.technical_decision || issue.resolution) && <p className="discussion-highlight-text">处理记录：{issue.technical_decision || issue.resolution}</p>}
        </article>)}
      </details>}
    </div>}
    {!!opinions.length && <details className="discussion-highlight-group discussion-highlight-opinions" open={!issues.length && !draft && !reviews.length}><summary>成员观点 · {opinions.length} 位</summary>
      {opinions.map((result) => <article key={result.id} className="discussion-highlight-card">
        <h4>{participantLabel(result.agent_id)}</h4>
        {result.data.position_summary && <Excerpt text={result.data.position_summary} />}
        <details><summary>主张与待验证事项</summary>
          <ul>{result.data.claims?.map((claim) => <li key={claim.id}>{claim.statement}<span className="discussion-evidence-label">{EVIDENCE[claim.evidence_status] ?? "待验证"}</span></li>)}</ul>
          {!!result.data.assumptions?.length && <><h4>判断前提</h4><ul>{result.data.assumptions.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
          {!!result.data.unknowns?.length && <><h4>尚不清楚</h4><ul>{result.data.unknowns.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
          {!!result.data.actions?.length && <><h4>建议行动</h4><ul>{result.data.actions.map((text, i) => <li key={i}>{text}</li>)}</ul></>}
        </details>
      </article>)}
    </details>}
    {draft && !ended && <div className="discussion-highlight-group"><h3>{d.final_id ? "综合建议" : "综合建议 · 待复核"} · {participantLabel(draft.agent_id)}</h3>
      {recommendation && <Excerpt text={recommendation.content} limit={420} />}
      <details><summary>查看共识、分歧与下一步</summary>
        {draft.data.sections?.filter((section) => section.id !== "recommendation").map((section) => <div key={section.id}><h4>{SECTIONS[section.id] ?? section.id}</h4><p className="discussion-highlight-text">{section.content}</p></div>)}
      </details>
    </div>}
    {!!reviews.length && <div className="discussion-highlight-group"><h3>复核重点</h3>
      {reviews.map((result) => <article className="discussion-highlight-card" key={result.id}><h4>{participantLabel(result.agent_id)}</h4>
        {result.data.no_findings ? <p>本次复核未发现遗漏或误读，不代表结论已验证。</p> : <ul>{result.data.findings?.map((finding) => <li key={finding.id}>{finding.blocking && <strong>需优先处理：</strong>}{finding.problem}<p>建议修正：{finding.desired_change}</p></li>)}</ul>}
      </article>)}
    </div>}
  </>;
  return ended ? <details className="discussion-highlights discussion-highlights-ended"><summary>查看本轮讨论过程</summary>{content}</details>
    : <section className="discussion-highlights" aria-label="讨论重点">{content}</section>;
}
