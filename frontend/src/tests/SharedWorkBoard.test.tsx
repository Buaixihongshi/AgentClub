import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { SharedWorkBoard } from '../components/SharedWorkBoard';
import { fetchSharedWork, listAgents, updateSharedWork } from '../lib/api';
import type { SharedWorkBoardView } from '../types';

vi.mock('../lib/api', () => ({ fetchSharedWork: vi.fn(), listAgents: vi.fn(), updateSharedWork: vi.fn() }));

const board: SharedWorkBoardView = {
  conversation_id: 'c_work', revision: 4, enabled: false, active_contract_id: 'wc_1',
  workspace_root: '/project', contracts: [{ id: 'wc_1', version: 1, source: '恢复后任务不丢失',
    goal: '可靠恢复', scope: '任务板', non_goals: '云同步', criteria: [{ id: 'AC-1', text: '保留检查点' }],
    validation_commands: [['npm', 'test']], digest: 'digest', activated_at: '2026-10-23' }],
  tasks: [], events: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchSharedWork).mockResolvedValue(structuredClone(board));
  vi.mocked(listAgents).mockResolvedValue([
    { id: 'chulan', display_name: '张楚岚', role: '伙伴', aliases: [], capability_hints: [] },
    { id: 'baobao', display_name: '冯宝宝', role: '伙伴', aliases: [], capability_hints: [] },
  ]);
  vi.mocked(updateSharedWork).mockResolvedValue({ ...structuredClone(board), revision: 5, enabled: true });
});

it('loads on demand and explicitly starts the approved contract', async () => {
  const user = userEvent.setup();
  const onUpdate = vi.fn();
  render(<SharedWorkBoard conversationId="c_work" goal="需求" onUpdate={onUpdate} />);
  expect(fetchSharedWork).not.toHaveBeenCalled();
  await user.click(screen.getByText('共享开发任务'));
  await user.click(await screen.findByRole('button', { name: '启动执行与本地提交' }));
  expect(updateSharedWork).toHaveBeenCalledWith('c_work', 'start', 4, {});
  expect(await screen.findByRole('button', { name: '暂停后续执行' })).toBeInTheDocument();
  expect(onUpdate).toHaveBeenCalled();
});

it('stores assignment with criterion and independent reviewer', async () => {
  const user = userEvent.setup();
  render(<SharedWorkBoard conversationId="c_work" goal="需求" />);
  await user.click(screen.getByText('共享开发任务'));
  await user.type(await screen.findByLabelText('任务名称'), '实现恢复');
  await user.selectOptions(screen.getByLabelText('执行人'), 'chulan');
  expect(screen.getByLabelText('独立审核人').querySelector('option[value="chulan"]')).toBeNull();
  await user.selectOptions(screen.getByLabelText('独立审核人'), 'baobao');
  await user.click(screen.getByRole('checkbox', { name: 'AC-1：保留检查点' }));
  await user.click(screen.getByRole('button', { name: '保存共享任务' }));
  expect(updateSharedWork).toHaveBeenCalledWith('c_work', 'create_task', 4, {
    title: '实现恢复', contract_id: 'wc_1', owner_id: 'chulan', reviewer_id: 'baobao', criterion_ids: ['AC-1'], depends_on: [],
  });
});

it('preserves user input and refreshes the board on revision conflict', async () => {
  vi.mocked(updateSharedWork).mockRejectedValueOnce(new Error('任务板已更新，请重新读取后操作'));
  const user = userEvent.setup();
  render(<SharedWorkBoard conversationId="c_work" goal="需求" />);
  await user.click(screen.getByText('共享开发任务'));
  await user.type(await screen.findByLabelText('任务名称'), '保留输入');
  await user.click(screen.getByRole('button', { name: '启动执行与本地提交' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('任务板已更新');
  expect(screen.getByLabelText('任务名称')).toHaveValue('保留输入');
  await waitFor(() => expect(fetchSharedWork).toHaveBeenCalledTimes(2));
});
