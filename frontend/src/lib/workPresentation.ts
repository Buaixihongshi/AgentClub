import type { SharedWorkBoardView, SharedWorkTask, WorkDecision } from '../types';

export const workStates: Record<string, string> = { ready: '等待执行', running: '正在推进', validating: '正在验证', review: '等待审核', done: '已交付', blocked: '需要处理', waiting: '等待处理', cancelled: '已取消' };
export const decisionKinds: Record<string, string> = { release: '上线审批', design: '设计选择', scope: '范围确认', budget: '执行预算' };
export const eventNames: Record<string, string> = {
  propose_work: '提出了任务提议', accept_proposal: '确认并启动了任务', reject_proposal: '选择继续讨论', create_work: '创建了任务', refine_agreement: '整理了验收约定', revise_work: '调整了需求', delegated: '邀请了协作者',
  request_decision: '发起了决定', decide: '记录了决定', submit: '提交了交付物', review: '提交了审核结论',
  pause: '暂停了后续推进', resume: '恢复了推进', cancel: '取消了任务', retry: '重新尝试执行', block: '记录了阻塞',
  progress: '更新了进展', continue_work: '安排了下一步', budget_exhausted: '执行预算已用尽',
  accept_transfer: '确认了责任移交', transfer: '提出了责任移交', release: '更新了发布状态',
};
export const shortVersion = (id?: string | null) => id?.replace(/^ws_/, '').slice(0, 8) || '尚无快照';
export const currentReview = (task: SharedWorkTask) => [...task.reviews].reverse().find(r => r.contract_id === task.contract_id && r.candidate_id && r.candidate_id === task.candidate?.id);
export function pendingDecisions(board: SharedWorkBoardView, root?: SharedWorkTask) {
  return (board.decisions ?? []).filter(d => d.status === 'pending' && (!root || board.tasks.some(t => t.id === d.task_id && (t.id === root.id || t.root_id === root.id))));
}
export function decisionMatches(board: SharedWorkBoardView, decision: WorkDecision) {
  const task = board.tasks.find(t => t.id === decision.task_id);
  if (!task || ['done', 'cancelled'].includes(task.status) || task.contract_id !== decision.contract_id) return false;
  // Ordinary decisions snapshot the workspace independently of a submitted
  // candidate. The server validates that snapshot when consuming the choice.
  // Release approval alone must also match the submitted candidate in this view.
  return decision.kind !== 'release' || !!decision.candidate_id && task.candidate?.id === decision.candidate_id;
}
export function taskStatus(task: SharedWorkTask, board: SharedWorkBoardView) {
  if (task.status === 'done') return task.release?.status === 'published' ? '已核对上线结果' : '已交付';
  if (task.status === 'cancelled') return '已取消';
  if (task.paused) return '已暂停';
  const pending = pendingDecisions(board, task);
  if (pending.some(d => d.kind === 'release')) return '等待你审批';
  if (pending.length) return '等待你决定';
  if (task.waiting_reason === 'budget') return '执行预算已用尽';
  if (task.waiting_reason === 'execution_failed') return '执行遇到问题';
  if (task.waiting_reason === 'external') return '等待外部条件';
  if (task.waiting_reason === 'release_unknown') return '发布结果待核对';
  if (['release_adapter', 'release_configuration'].includes(task.waiting_reason ?? '') || task.release?.status === 'awaiting_adapter') return '等待发布配置';
  if (task.waiting_reason === 'review' || task.status === 'review') return '等待审核';
  return workStates[task.status] ?? task.status;
}
export function taskSummary(task: SharedWorkTask, board: SharedWorkBoardView, label: (id: string) => string) {
  const review = currentReview(task);
  if (task.release?.status === 'published') return '发布结果已核对，相关证据已保存。';
  if (task.release?.status === 'awaiting_adapter') return '此版本已批准，等待项目发布入口。';
  if (task.paused) return task.active_mission_id ? '当前动作正在收尾，后续动作已暂停。' : '后续动作已暂停，恢复后继续推进。';
  if (review?.decision === 'approved' && pendingDecisions(board, task).some(d => d.kind === 'release')) return `${label(review.reviewer_id)}已通过当前快照复审，尚未获得上线批准。`;
  if (review?.decision === 'changes_requested') return `${label(review.reviewer_id)}提出了修改意见，等待补充后再审。`;
  return task.blocker || task.next_action || task.progress || '进展会保存在任务记录中。';
}
export interface WorkActivity {
  id: string; actor: string; title: string; createdAt: string; detail: string; action?: string;
  taskId?: string; missionId?: string; candidateId?: string; contractId?: string; decisionId?: string;
}
export function workActivities(board: SharedWorkBoardView): WorkActivity[] {
  const events: WorkActivity[] = board.events.filter(e => ['accept_proposal', 'create_work', 'refine_agreement', 'revise_work', 'delegated', 'submit', 'review', 'block', 'cancel', 'transfer', 'accept_transfer'].includes(e.action)).map(e => {
    const task = board.tasks.find(t => t.id === e.task_id);
    const review = e.action === 'review' ? task?.reviews.find(r => r.mission_id === e.mission_id) : undefined;
    const evidence = task?.evidence.find(v => v.mission_id === e.mission_id);
    return { id: e.id, action: e.action, actor: e.actor, title: review ? review.decision === 'approved' ? '通过了候选快照复审' : '要求补充修改与验证' : e.action === 'submit' && task?.submission?.kind === 'design' ? '交付了设计方案' : eventNames[e.action],
      createdAt: e.created_at, detail: review?.note || e.reason || task?.title || '', taskId: e.task_id, missionId: e.mission_id,
      candidateId: review?.candidate_id || evidence?.candidate_id, contractId: review?.contract_id || evidence?.contract_id };
  });
  for (const d of board.decisions ?? []) {
    if (d.status !== 'answered') continue;
    const text = d.options.find(o => o.id === d.option_id)?.text ?? d.option_id ?? '已记录的选项';
    const concise = text.split(/[。；;\n]/)[0];
    events.push({ id: d.id, actor: d.actor || 'human', title: `选择了${concise.replace(/^选择\s*/, ' ')}`, createdAt: d.answered_at || d.created_at || '',
      detail: d.note || d.question, taskId: d.task_id, candidateId: d.candidate_id, contractId: d.contract_id, decisionId: d.id });
  }
  return events.sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt));
}
export function artifactName(path: string) {
  const file = path.split('/').pop() || path;
  if (/^index\.html?$/.test(file)) return '交互预览';
  if (/design-([ab])\.html?/i.test(file)) return `方案 ${file.match(/design-([ab])/i)![1].toUpperCase()}`;
  if (/^spec\.md$/.test(file)) return '需求说明';
  if (/host-browser-check/.test(file)) return '浏览器验证';
  if (/implementation-check/.test(file)) return '实现验证';
  if (/blocker/.test(file)) return '历史验证限制';
  if (/\.test\./.test(file)) return '回归测试';
  if (/\.(png|jpe?g|webp)$/i.test(file)) return '浏览器截图';
  return file;
}
export function snapshotUrl(conversationId: string, version: string, path: string) {
  return `/api/conversations/${encodeURIComponent(conversationId)}/work/snapshots/${encodeURIComponent(version)}/file?path=${encodeURIComponent(path)}`;
}
