import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { SharedWorkWorkspace } from '../components/SharedWorkWorkspace';
import { fetchSharedWork, listAgents, updateSharedWork } from '../lib/api';
import type { SharedWorkBoardView } from '../types';
vi.mock('../lib/api', () => ({ fetchSharedWork: vi.fn(), listAgents: vi.fn(), updateSharedWork: vi.fn() }));
const board: SharedWorkBoardView = {
  protocol: 'adaptive_v1', conversation_id: 'c_ui', revision: 8, enabled: true, active_contract_id: 'wc1', workspace_root: '/project',
  contracts: [{ id: 'wc1', version: 1, source: '需求', goal: '修复导出', scope: '导出', non_goals: '', criteria: [], validation_commands: [], digest: 'digest', activated_at: null }],
  tasks: [{ id: 't1', root_id: 't1', parent_id: null, owner_id: 'chulan', reviewer_id: 'wangye', title: '修复导出', contract_id: 'wc1', status: 'waiting', progress: '旧的进展', next_action: '旧的下一步',
    criterion_ids: [], depends_on: [], blocker: null, active_mission_id: null, candidate_sha: null, candidate: { id: 'ws_new', base_sha: 'base' },
    reviews: [{ id: 'r1', contract_id: 'wc1', candidate_id: 'ws_new', criterion_ids: [], commit_sha: '', evidence_id: 'e1', mission_id: 'm1', reviewer_id: 'wangye', decision: 'approved', note: '复审通过' }], evidence: [], attempts: [] }],
  decisions: [{ id: 'd1', task_id: 't1', contract_id: 'wc1', kind: 'release', status: 'pending', candidate_id: 'ws_new', environment: 'test', question: '批准这个版本？', options: [{ id: 'approve', text: '批准此版本' }], artifact_paths: ['docs/index.html'] }], events: [],
};
beforeEach(() => {
  vi.resetAllMocks(); vi.mocked(fetchSharedWork).mockResolvedValue(structuredClone(board));
  vi.mocked(listAgents).mockResolvedValue([{ id: 'chulan', display_name: '张楚岚', aliases: [], role: '', capability_hints: [] }, { id: 'wangye', display_name: '王也', aliases: [], role: '', capability_hints: [] }]);
  vi.mocked(updateSharedWork).mockRejectedValue(new Error('版本已变化，请重新核对'));
});
const onOpenTask = vi.fn();
function setup() { onOpenTask.mockClear(); return render(<SharedWorkWorkspace conversationId="c_ui" goal="" archived={false} onOpenTask={onOpenTask} chat={<div>真实对话<input aria-label="测试草稿" defaultValue="未发送草稿" /></div>} composer={<div>输入区</div>} />); }
it('会话只保留任务摘要和跳转，任务详情不再嵌在会话内', async () => {
  setup(); await screen.findByText('1 项待你处理');
  expect(screen.getByText('王也已通过当前快照复审，尚未获得上线批准。')).toBeVisible();
  expect(screen.queryByRole('tab', { name: /^任务/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: '关键记录' })).not.toBeInTheDocument();
  expect(screen.queryByTitle('方案预览：docs/index.html')).not.toBeInTheDocument();
  const draft = screen.getByLabelText('测试草稿'); fireEvent.change(draft, { target: { value: '继续编辑中的草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '查看并决定' }));
  expect(onOpenTask).toHaveBeenCalledWith('t1');
  expect(screen.getByLabelText('测试草稿')).toBe(draft);
  expect(draft).toHaveValue('继续编辑中的草稿');
});
it('子任务的待决定事项跳到所属根任务，不能误开另一项工作', async () => {
  const next = structuredClone(board);
  next.tasks.push({ ...structuredClone(next.tasks[0]), id: 'root2', root_id: 'root2', title: '另一项工作' });
  next.tasks.push({ ...structuredClone(next.tasks[0]), id: 'child2', root_id: 'root2', parent_id: 'root2', title: '协作步骤' });
  next.decisions![0].task_id = 'child2';
  vi.mocked(fetchSharedWork).mockResolvedValue(next);
  setup(); await screen.findByText('1 项待你处理');
  fireEvent.click(screen.getByRole('button', { name: '查看并决定' }));
  expect(onOpenTask).toHaveBeenCalledWith('root2');
});
it('旧轮询响应不能覆盖较新的任务状态', async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: SharedWorkBoardView) => void;
  try {
    setup(); await act(async () => { await Promise.resolve(); });
    expect(screen.getByText('1 项待你处理')).toBeInTheDocument();
    vi.mocked(fetchSharedWork).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    const newer = structuredClone(board); newer.revision = 10; newer.decisions = [];
    vi.mocked(fetchSharedWork).mockResolvedValue(newer);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.queryByText('1 项待你处理')).not.toBeInTheDocument();
    await act(async () => { resolveOld(structuredClone(board)); });
    expect(screen.queryByText('1 项待你处理')).not.toBeInTheDocument();
    expect(screen.getByLabelText('测试草稿')).toHaveValue('未发送草稿');
  } finally { vi.useRealTimers(); }
});
it('会话仍通过具体提议 ID 确认工作，拒绝把普通聊天自动当授权', async () => {
  const empty = structuredClone(board); empty.tasks = []; empty.contracts = []; empty.decisions = [];
  empty.proposals = [{ id: 'wp_exact', title: '小程序页面方案', owner_id: 'chulan', created_by: 'chulan', status: 'pending', mission_id: 'm_intake', turn_id: 'turn_intake', created_at: '', digest: 'exact', agreement: { ...board.contracts[0], goal: '设计页面方案', delivery: 'document', criteria: [{id: 'AC-1', text: '展示页面结构与交互'}] } }];
  vi.mocked(fetchSharedWork).mockResolvedValue(empty);
  const accepted = structuredClone(empty); accepted.revision++; accepted.proposals![0].status = 'accepted';
  vi.mocked(updateSharedWork).mockResolvedValue(accepted);
  setup(); await screen.findByText('张楚岚整理了一项任务提议');
  expect(updateSharedWork).not.toHaveBeenCalled();
  expect(screen.queryByRole('tab', { name: '任务' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /查看提议/ }));
  expect(screen.getByText('方案与文档')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '确认并开始' }));
  await waitFor(() => expect(updateSharedWork).toHaveBeenCalledWith('c_ui', 'accept_proposal', 8, { proposal_id: 'wp_exact' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByLabelText('测试草稿')).toHaveValue('未发送草稿');
});
