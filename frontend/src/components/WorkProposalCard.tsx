import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import type { WorkProposal } from '../types';
import { WorkDialog } from './WorkDialog';

const deliveryNames: Record<string, string> = { document: '方案与文档', development: '开发与验证', online: '开发完成后申请上线' };

/** The conversation owns intake; this card confirms a saved proposal, never edits its authority. */
export function WorkProposalCard({ proposal, label, busy, archived, error, command }: {
  proposal: WorkProposal; label: (id: string) => string; busy: boolean; archived: boolean; error: string;
  command: (action: string, data?: Record<string, unknown>) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const agreement = proposal.agreement;
  return <>
    <div className="work-proposal-card">
      <div><strong>{label(proposal.created_by)}整理了一项任务提议</strong><span>{proposal.title}</span></div>
      <button className="work-primary" onClick={() => setOpen(true)}>查看提议 <ArrowRight size={16} /></button>
    </div>
    {open && <WorkDialog title="确认任务提议" onClose={() => setOpen(false)}>
      <h3>{proposal.title}</h3><p className="work-prose">{agreement.goal}</p>
      <dl><dt>负责人</dt><dd>{label(proposal.owner_id)}</dd><dt>交付内容</dt><dd>{deliveryNames[agreement.delivery ?? 'document']}</dd><dt>本次范围</dt><dd>{agreement.scope}</dd></dl>
      {agreement.non_goals && <p>本次不做：{agreement.non_goals}</p>}
      <h3>怎样算完成</h3><ul>{agreement.criteria.map(c => <li key={c.id}>{c.text}</li>)}</ul>
      <p className="work-muted">确认后，团队按此范围持续推进。生产上线仍需你批准具体版本。</p>
      {error && <p className="work-error" role="alert">{error}</p>}
      <div className="work-decision-buttons">
        <button className="work-primary" disabled={busy || archived} onClick={() => void command('accept_proposal', { proposal_id: proposal.id }).then(ok => { if (ok) setOpen(false); })}>确认并开始</button>
        <button disabled={busy || archived} onClick={() => void command('reject_proposal', { proposal_id: proposal.id }).then(ok => { if (ok) { setOpen(false); requestAnimationFrame(() => document.getElementById('mission-input')?.focus()); } })}>继续讨论</button>
      </div>
      <details className="work-file-source"><summary>提议来源与版本</summary><p className="work-prose">{agreement.source}</p><p>对话轮次 {proposal.mission_id}</p><p>角色回合 {proposal.turn_id}</p><p>提议 {proposal.id} · {proposal.digest}</p></details>
    </WorkDialog>}
  </>;
}
