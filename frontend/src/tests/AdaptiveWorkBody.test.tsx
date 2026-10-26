import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AdaptiveWorkBody } from '../components/AdaptiveWorkBody';
import type { SharedWorkBoardView } from '../types';

const board: SharedWorkBoardView = {
  protocol: 'adaptive_v1', conversation_id: 'c_work', revision: 4, enabled: true, active_contract_id: 'wc_1',
  workspace_root: '/project', events: [], contracts: [{ id: 'wc_1', version: 1, source: '修复导出乱码', goal: '修复导出乱码',
    scope: '导出功能', non_goals: '', criteria: [{ id: 'AC-1', text: '中文正确' }], validation_commands: [], digest: 'digest',
    activated_at: null, delivery: 'development', risks: ['local'] }],
  tasks: [{ id: 'wt_root', root_id: 'wt_root', parent_id: null, title: '导出修复', owner_id: 'chulan', reviewer_id: '',
    contract_id: 'wc_1', criterion_ids: ['AC-1'], depends_on: [], status: 'ready', progress: '已定位问题', next_action: '验证真实导出文件',
    blocker: null, active_mission_id: null, attempts: [], candidate_sha: null, evidence: [], reviews: [], attempts_used: 1, max_attempts: 20 }],
  decisions: [],
};
const agents = [{ id: 'chulan', display_name: '张楚岚' }, { id: 'wangye', display_name: '王也' }];

describe('自主工作体验', () => {
  it('简单工作直接展示责任与下一步，不要求分配设计和审核', () => {
    render(<AdaptiveWorkBody board={board} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} />);
    expect(screen.getByText(/负责人 张楚岚/)).toBeInTheDocument();
    expect(screen.getByText('验证真实导出文件')).toBeInTheDocument();
    expect(screen.queryByText('独立审核人')).not.toBeInTheDocument();
    expect(screen.queryByText('启用约定')).not.toBeInTheDocument();
  });

  it('UI 方案可见，并把具体选择及反馈交还同一决定卡', async () => {
    const command = vi.fn().mockResolvedValue(true);
    const design = structuredClone(board);
    design.tasks[0].candidate = { id: 'ws_exact', base_sha: 'base' };
    design.decisions = [{ id: 'wd_ui', task_id: 'wt_root', kind: 'design', question: '选择报名页布局',
      options: [{ id: 'a', text: '选择 A' }, { id: 'b', text: '选择 B' }], status: 'pending',
      contract_id: 'wc_1', candidate_id: 'ws_exact', artifact_paths: ['docs/a.html', 'docs/b.html'] }];
    render(<AdaptiveWorkBody board={design} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={command} />);
    expect(screen.queryByTitle('方案预览：docs/a.html')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /查看并决定/ }));
    fireEvent.click(screen.getByRole('button', { name: /a.html/ }));
    expect(screen.getByTitle('方案预览：docs/a.html')).toHaveAttribute('sandbox', 'allow-scripts allow-forms');
    expect(screen.getByTitle('方案预览：docs/a.html')).toHaveAttribute('src', expect.stringContaining('ws_exact'));
    fireEvent.click(screen.getByRole('button', { name: '关闭a.html' }));
    fireEvent.change(screen.getByLabelText('反馈或修改意见（可选）'), { target: { value: '按钮放底部' } });
    fireEvent.click(screen.getByRole('button', { name: '选择 B' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith('decide', { decision_id: 'wd_ui', option_id: 'b', note: '按钮放底部' }));
  });

  it('已失效的方案没有可批准按钮', () => {
    const stale = structuredClone(board);
    stale.decisions = [{ id: 'wd_old', task_id: 'wt_root', kind: 'release', question: '发布旧版', options: [{ id: 'approve', text: '批准此版本上线' }],
      status: 'stale', contract_id: 'wc_1', artifact_paths: [], candidate_id: 'ws_old', environment: 'production' }];
    render(<AdaptiveWorkBody board={stale} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} />);
    fireEvent.click(screen.getByText('历史决定'));
    fireEvent.click(screen.getByRole('button', { name: '已失效的决定' }));
    expect(screen.getByText('此决定对应的版本已失效，请等待新的决定。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '批准此版本上线' })).not.toBeInTheDocument();
  });

  it.each(['direction', 'design'])('未交付时的 %s 决定仍展示 A/B/C，并提交对应选项', async kind => {
    const pending = structuredClone(board);
    pending.tasks[0].status = 'waiting';
    pending.tasks[0].candidate = null;
    pending.decisions = [{ id: 'wd_path', task_id: 'wt_root', kind, question: '请选择实施路径',
      options: [{ id: 'copy', text: 'B：先审计再实施' }, { id: 'fresh', text: 'A：从零实施' }, { id: 'wait', text: 'C：等待权限' }],
      status: 'pending', contract_id: 'wc_1', candidate_id: 'ws_question', artifact_paths: [] }];
    const command = vi.fn().mockResolvedValue(true);
    render(<AdaptiveWorkBody board={pending} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={command} />);
    fireEvent.click(screen.getByRole('button', { name: /查看并决定/ }));
    expect(screen.queryByText('此决定对应的版本已失效，请等待新的决定。')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A：从零实施' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'C：等待权限' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'B：先审计再实施' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith('decide', { decision_id: 'wd_path', option_id: 'copy', note: '' }));
  });

  it('新的方向决定不要求沿用上次交付的候选快照', () => {
    const pending = structuredClone(board);
    pending.tasks[0].candidate = { id: 'ws_previous_delivery', base_sha: 'base' };
    pending.decisions = [{ id: 'wd_new', task_id: 'wt_root', kind: 'direction', question: '下一步方向',
      options: [{ id: 'continue', text: '继续当前方向' }], status: 'pending',
      contract_id: 'wc_1', candidate_id: 'ws_new_question', artifact_paths: [] }];
    render(<AdaptiveWorkBody board={pending} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /查看并决定/ }));
    expect(screen.getByRole('button', { name: '继续当前方向' })).toBeEnabled();
  });

  it.each(['missing-candidate', 'different-candidate', 'different-contract', 'cancelled-task'])('保留失效决定的限制：%s', scenario => {
    const pending = structuredClone(board);
    pending.tasks[0].candidate = scenario === 'missing-candidate' ? null : { id: 'ws_current', base_sha: 'base' };
    if (scenario === 'cancelled-task') pending.tasks[0].status = 'cancelled';
    pending.decisions = [{ id: 'wd_invalid', task_id: 'wt_root', kind: scenario === 'different-contract' || scenario === 'cancelled-task' ? 'direction' : 'release',
      question: '不可继续的决定', options: [{ id: 'approve', text: '确认此决定' }], status: 'pending',
      contract_id: scenario === 'different-contract' ? 'wc_old' : 'wc_1',
      candidate_id: scenario === 'different-candidate' ? 'ws_old' : 'ws_current', artifact_paths: [] }];
    render(<AdaptiveWorkBody board={pending} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} requestedDecision="wd_invalid" />);
    expect(screen.getByText('此决定对应的版本已失效，请等待新的决定。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认此决定' })).not.toBeInTheDocument();
  });

  it('暂停和取消直接操作根责任，已归档工作仍可受控暂停', async () => {
    const command = vi.fn().mockResolvedValue(true);
    render(<AdaptiveWorkBody board={board} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived command={command} />);
    fireEvent.click(screen.getByLabelText('任务操作'));
    fireEvent.click(screen.getByRole('button', { name: '暂停任务' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith('pause', { task_id: 'wt_root' }));
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    expect(command).toHaveBeenCalledWith('cancel', { task_id: 'wt_root' });
  });
  it('新需求回到对话，不在进度面板要求填写任务表', () => {
    const onDiscuss = vi.fn();
    render(<AdaptiveWorkBody board={board} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} onDiscuss={onDiscuss} />);
    fireEvent.click(screen.getByLabelText('任务操作'));
    fireEvent.click(screen.getByRole('button', { name: '在对话中提出新需求' }));
    expect(onDiscuss).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText('要交付什么')).not.toBeInTheDocument();
  });
  it('展示绑定工作区、打开预览并停止对应任务的预览', async () => {
    const bound = structuredClone(board);
    bound.tasks[0].environment = { status: 'ready', branch: 'codex/task-example', base_sha: 'base',
      preview: { status: 'ready', url: 'http://127.0.0.1:5199', candidate_id: 'ws_current' } };
    const command = vi.fn().mockResolvedValue(true);
    render(<AdaptiveWorkBody board={bound} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={command} />);
    expect(screen.getByRole('link', { name: '打开预览' })).toHaveAttribute('href', 'http://127.0.0.1:5199/');
    fireEvent.click(screen.getByRole('button', { name: '停止预览' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith('stop_preview', { task_id: 'wt_root' }));
    expect(screen.queryByLabelText('要交付什么')).not.toBeInTheDocument();
  });
  it('工作区失败可重试，其他任务执行时显示排队原因', async () => {
    const failed = structuredClone(board);
    failed.tasks[0].environment = { status: 'failed', failure_reason: '目录被占用' };
    failed.execution = { active: [{ conversation_id: 'other', task_id: 'other-task', title: '上传文件' }] };
    const command = vi.fn().mockResolvedValue(true);
    render(<AdaptiveWorkBody board={failed} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={command} />);
    expect(screen.getByText(/另一项工作「上传文件」正在执行/)).toBeInTheDocument();
    expect(screen.getByText('目录被占用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试准备工作区' }));
    await waitFor(() => expect(command).toHaveBeenCalledWith('retry_environment', { task_id: 'wt_root' }));
  });
  it('拒绝非本机预览链接，准备中不重复启动', () => {
    const pending = structuredClone(board);
    pending.tasks[0].environment = { status: 'ready', preview: { status: 'starting', url: 'javascript:alert(1)' } };
    render(<AdaptiveWorkBody board={pending} conversationId="c_work" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} />);
    expect(screen.queryByRole('link', { name: '打开预览' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '正在准备预览…' })).toBeDisabled();
  });
});
