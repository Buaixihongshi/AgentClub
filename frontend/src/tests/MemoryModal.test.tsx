import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { MemoryModal } from '../components/MemoryModal';
import {
  clearMemoryCheckpoints,
  fetchAgentMemory,
  fetchMemoryCheckpoints,
  fetchMemoryHandovers,
  fetchMemoryOverview,
  purgeMemoryEntry,
  rebuildMemoryIndex,
  setMemoryEntryStatus,
  updateMemoryEntry,
} from '../lib/api';

vi.mock('../lib/api', () => ({
  fetchMemoryOverview: vi.fn(),
  fetchAgentMemory: vi.fn(),
  fetchMemoryCheckpoints: vi.fn(),
  fetchMemoryHandovers: vi.fn(),
  clearMemoryCheckpoints: vi.fn(),
  updateMemoryEntry: vi.fn(),
  setMemoryEntryStatus: vi.fn(),
  purgeMemoryEntry: vi.fn(),
  rebuildMemoryIndex: vi.fn(),
}));

const overview = {
  memory_root: '/state/data/memory',
  consistency: { files: 2, indexed: 2, stale: 0, missing_files: 0, applied: false, active_by_agent: {} },
  agents: [
    { id: 'chulan', display_name: '张楚岚', role: '架构', active: 1, forgotten: 1 },
    { id: 'baobao', display_name: '冯宝宝', role: '审查', active: 0, forgotten: 0 },
  ],
};
const entry = {
  id: 'mem_1', agent_id: 'chulan', title: '用户偏好', tags: ['偏好'],
  status: 'active', revision: 1, created_at: '2026-10-25T00:00:00+00:00',
  updated_at: '2026-10-25T00:00:00+00:00', source_refs: [], body: '偏好简洁中文回复。',
};
const forgottenEntry = { ...entry, id: 'mem_2', title: '过期事实', status: 'forgotten', body: '已被新事实取代。' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchMemoryOverview).mockResolvedValue(structuredClone(overview));
  vi.mocked(fetchAgentMemory).mockResolvedValue({ entries: [structuredClone(entry)] });
  vi.mocked(fetchMemoryCheckpoints).mockResolvedValue({
    checkpoints: [{ conversation_id: 'c1', agent_id: 'chulan', text: '等待用户确认。', source_message_ids: [], cursor_event_id: 3, updated_at: '2026-10-25T00:00:00+00:00' }],
  });
  vi.mocked(fetchMemoryHandovers).mockResolvedValue({ handovers: [] });
  vi.mocked(setMemoryEntryStatus).mockResolvedValue({ id: 'mem_1', status: 'forgotten' });
  vi.mocked(purgeMemoryEntry).mockResolvedValue({ id: 'mem_2', purged: true });
  vi.mocked(updateMemoryEntry).mockResolvedValue({ id: 'mem_1', revision: 2, updated_at: '2026-10-25T01:00:00+00:00' });
  vi.mocked(rebuildMemoryIndex).mockResolvedValue(structuredClone(overview.consistency));
  vi.mocked(clearMemoryCheckpoints).mockResolvedValue({ removed: 1 });
});

it('lists agents and active entries with consistency status', async () => {
  render(<MemoryModal conversationId="c1" onClose={() => {}} />);
  expect(await screen.findByText('张楚岚')).toBeInTheDocument();
  expect(await screen.findByText('用户偏好')).toBeInTheDocument();
  expect(screen.getByText(/文件 2 · 索引 2/)).toBeInTheDocument();
  expect(await screen.findByText('等待用户确认。')).toBeInTheDocument();
});

it('forget hides an entry after switching to show forgotten', async () => {
  const user = userEvent.setup();
  render(<MemoryModal conversationId={null} onClose={() => {}} />);
  await user.click(await screen.findByRole('button', { name: '忘记' }));
  expect(setMemoryEntryStatus).toHaveBeenCalledWith('mem_1', 'forgotten');
  await waitFor(() => expect(fetchAgentMemory).toHaveBeenCalledWith('chulan', false));
});

it('edits an entry body and saves a revision', async () => {
  const user = userEvent.setup();
  render(<MemoryModal conversationId={null} onClose={() => {}} />);
  await user.click(await screen.findByRole('button', { name: '修订' }));
  const body = await screen.findByLabelText('记忆正文');
  await user.clear(body);
  await user.type(body, '修订后的偏好描述。');
  await user.click(screen.getByRole('button', { name: '保存修订' }));
  expect(updateMemoryEntry).toHaveBeenCalledWith('mem_1', { title: '用户偏好', body: '修订后的偏好描述。' });
});

it('shows forgotten entries only when toggled', async () => {
  vi.mocked(fetchAgentMemory).mockResolvedValue({ entries: [structuredClone(forgottenEntry)] });
  const user = userEvent.setup();
  render(<MemoryModal conversationId={null} onClose={() => {}} />);
  expect(await screen.findByText('过期事实')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '恢复' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '清除' }));
  expect(purgeMemoryEntry).toHaveBeenCalledWith('mem_2');
});

it('rebuilds the index on demand', async () => {
  const user = userEvent.setup();
  render(<MemoryModal conversationId={null} onClose={() => {}} />);
  await user.click(await screen.findByRole('button', { name: '重建索引' }));
  expect(rebuildMemoryIndex).toHaveBeenCalled();
});

it('clears one checkpoint for the conversation', async () => {
  const user = userEvent.setup();
  render(<MemoryModal conversationId="c1" onClose={() => {}} />);
  await user.click(await screen.findByRole('button', { name: '清除 chulan 的检查点' }));
  expect(clearMemoryCheckpoints).toHaveBeenCalledWith('c1', 'chulan');
});
