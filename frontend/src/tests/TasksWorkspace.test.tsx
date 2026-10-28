import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdaptiveWorkBody } from '../components/AdaptiveWorkBody';
import { TasksWorkspace } from '../components/TasksWorkspace';
import { WorkflowGraph } from '../components/WorkflowGraph';
import { NewTaskDialog } from '../components/NewTaskDialog';
import { workflowError } from '../components/TaskOrchestrationForm';
import { commandTask, createTask, fetchTask, listTasks } from '../lib/tasks';
import { fetchSharedWork, listAgents } from '../lib/api';
import type { SharedWorkTask, TaskDetail, TaskSummary } from '../types';
vi.mock('../lib/tasks', () => ({ commandTask: vi.fn(), createTask: vi.fn(), fetchTask: vi.fn(), listTasks: vi.fn() }));
vi.mock('../lib/api', async original => ({ ...await original<typeof import('../lib/api')>(), listAgents: vi.fn(), fetchSharedWork: vi.fn() }));
const agents = [{ id: 'chulan', display_name: '张楚岚' }, { id: 'baobao', display_name: '冯宝宝' }];
function taskDetail(id = 'root_a'): TaskDetail {
  const task = { id, root_id: id, parent_id: null, title: `任务 ${id}`, owner_id: 'chulan', reviewer_id: '', contract_id: 'agreement',
    criterion_ids: ['AC-1'], depends_on: [], status: 'ready' as const, paused: true, progress: '', next_action: '', blocker: null,
    active_mission_id: null, attempts: [], candidate_sha: null, evidence: [], reviews: [], attempts_used: 0, max_attempts: 20 };
  return { task, source_conversation_id: null, execution_conversation_id: `execution_${id}`,
    orchestration: { mode: 'workflow', steps: [{ id: 'investigate', title: '调查现状', owner_id: 'chulan', goal: '核对输入与现有行为', depends_on: [], task_id: 'step_a' }, { id: 'implement', title: '按方案实施', owner_id: 'baobao', goal: '实现交付目标', depends_on: ['investigate'] }] },
    board: { protocol: 'adaptive_v1', conversation_id: `execution_${id}`, revision: 4, enabled: true, active_contract_id: 'agreement', workspace_root: '/project',
      tasks: [task], contracts: [{ id: 'agreement', version: 1, source: '原始需求', goal: '交付可核对的结果', scope: '当前项目', non_goals: '', criteria: [{id: 'AC-1', text: '满足需求'}], validation_commands: [], digest: 'exact', activated_at: null, delivery: 'development' }], decisions: [], events: [] } };
}
function summary(detail: TaskDetail): TaskSummary { return { id: detail.task.id, title: detail.task.title, owner_id: detail.task.owner_id, status: detail.task.status, paused: !!detail.task.paused, delivery: 'development', created_at: '', updated_at: '', conversation_id: detail.execution_conversation_id, source_conversation_id: null, orchestration: detail.orchestration, pending_decisions: 0 }; }
beforeEach(() => {
  vi.resetAllMocks();
  const value = taskDetail();
  vi.mocked(fetchTask).mockResolvedValue(value);
  vi.mocked(listTasks).mockResolvedValue([summary(value)]);
  vi.mocked(listAgents).mockResolvedValue(agents.map(a => ({ ...a, aliases: [], role: '', capability_hints: [] })));
});
describe('独立任务工作区', () => {
  it('没有会话也能新建，保存不会自动执行', async () => {
    vi.mocked(listTasks).mockResolvedValue([]);
    const created = taskDetail('created'); vi.mocked(createTask).mockResolvedValue(created);
    const onSelect = vi.fn();
    render(<TasksWorkspace onSelectTask={onSelect} onOpenConversation={vi.fn()} agents={agents} />);
    fireEvent.click(screen.getAllByRole('button', { name: /新建任务/ })[0]);
    const form = screen.getByRole('dialog', { name: '新建任务' });
    fireEvent.change(within(form).getByLabelText('任务名称'), { target: { value: '修复导出' } });
    fireEvent.change(within(form).getByLabelText('要交付什么'), { target: { value: '中文导出内容正确' } });
    fireEvent.change(within(form).getByLabelText('怎样算完成（每行一项）'), { target: { value: '中文文件可打开' } });
    fireEvent.click(within(form).getByRole('button', { name: '保存任务' }));
    await waitFor(() => expect(createTask).toHaveBeenCalledOnce());
    expect(createTask).toHaveBeenCalledWith(expect.objectContaining({ title: '修复导出', paused: true, orchestration: expect.objectContaining({ mode: 'workflow' }) }));
    expect(commandTask).not.toHaveBeenCalled(); expect(onSelect).toHaveBeenCalledWith('created');
  });
  it('伙伴加载失败明确提示，并在重试成功前禁止新建', async () => {
    vi.mocked(listAgents).mockRejectedValueOnce(new Error('伙伴服务暂时不可用'));
    vi.mocked(listTasks).mockResolvedValue([]);
    render(<TasksWorkspace onSelectTask={vi.fn()} onOpenConversation={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('伙伴服务暂时不可用');
    expect(screen.getAllByRole('button', { name: /新建任务/ }).every(button => (button as HTMLButtonElement).disabled)).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '重新读取伙伴' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: /新建任务/ }).every(button => !(button as HTMLButtonElement).disabled)).toBe(true));
  });
  it('独立任务只保留一份标题和暂停控制，保留需求与交付入口', async () => {
    const detail = taskDetail(); detail.task.paused = false;
    vi.mocked(fetchTask).mockResolvedValue(detail);
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    expect(await screen.findByRole('heading', { level: 1, name: '任务 root_a' })).toBeVisible();
    expect(screen.queryByRole('heading', { level: 2, name: '任务 root_a' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '暂停任务' })).toHaveLength(1);
    expect(screen.getByRole('button', { name: '查看需求' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '交付与记录' })).toBeInTheDocument();
  });
  it('未开始的暂停任务不把待准备工作区显示为正在执行', async () => {
    const detail = taskDetail(); detail.task.environment = { status: 'pending' };
    vi.mocked(fetchTask).mockResolvedValue(detail);
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    expect(await screen.findByText('开始执行后准备')).toBeVisible();
    expect(within(screen.getByLabelText('任务工作区与预览')).queryByText('正在准备')).not.toBeInTheDocument();
    expect(commandTask).not.toHaveBeenCalled();
  });
  it('历史共享任务在独立任务页保留原控制，不显示新流程动作', async () => {
    const detail = taskDetail('legacy:conversation'); detail.protocol = 'legacy'; detail.archived = true;
    const legacyBoard = structuredClone(detail.board); delete legacyBoard.protocol;
    detail.task = { id: detail.task.id, title: detail.task.title, owner_id: 'chulan', status: 'ready', paused: true } as SharedWorkTask;
    vi.mocked(fetchSharedWork).mockResolvedValue(legacyBoard);
    vi.mocked(fetchTask).mockResolvedValue(detail);
    render(<TasksWorkspace selectedTaskId="legacy:conversation" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    expect(await screen.findByText('保留原有约定、控制和证据')).toBeVisible();
    expect(await screen.findByText('共享开发任务')).toBeVisible();
    expect(screen.queryByRole('button', { name: '调整编排' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '开始任务' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('任务工作流')).not.toBeInTheDocument();
  });
  it('以所选根任务加载详情，晚到的上一任务响应不会替换当前任务', async () => {
    let resolveOld!: (value: TaskDetail) => void;
    vi.mocked(fetchTask).mockImplementation(id => id === 'root_a' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(taskDetail('root_b')));
    const props = { onSelectTask: vi.fn(), onOpenConversation: vi.fn(), agents };
    const { rerender } = render(<TasksWorkspace {...props} selectedTaskId="root_a" />);
    rerender(<TasksWorkspace {...props} selectedTaskId="root_b" />);
    await screen.findByRole('heading', { level: 1, name: '任务 root_b' });
    await act(async () => { resolveOld(taskDetail('root_a')); });
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('任务 root_b');
    expect(screen.queryByRole('heading', { level: 1, name: '任务 root_a' })).not.toBeInTheDocument();
  });
  it('开始动作使用任务入口和当前revision，只有用户点击才恢复执行', async () => {
    const detail = taskDetail(); const started = structuredClone(detail); started.task.paused = false; started.board.revision++;
    vi.mocked(commandTask).mockResolvedValue(started);
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    const button = await screen.findByRole('button', { name: '开始任务' });
    expect(commandTask).not.toHaveBeenCalled(); fireEvent.click(button);
    await waitFor(() => expect(commandTask).toHaveBeenCalledWith('root_a', 'resume', 4, { task_id: 'root_a' }, expect.any(String)));
  });
  it('编排保存冲突后保留步骤草稿，并读取服务端最新revision', async () => {
    const original = taskDetail(); const next = taskDetail(); next.board.revision = 8;
    vi.mocked(fetchTask).mockResolvedValue(original);
    vi.mocked(commandTask).mockRejectedValue(new Error('任务版本已变化'));
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    fireEvent.click(await screen.findByRole('button', { name: '调整编排' }));
    const dialog = screen.getByRole('dialog', { name: '调整任务编排' });
    fireEvent.change(within(dialog).getAllByLabelText('步骤名称')[0], { target: { value: '保留我的调查草稿' } });
    vi.mocked(fetchTask).mockResolvedValue(next);
    fireEvent.click(within(dialog).getByRole('button', { name: '保存编排' }));
    await waitFor(() => expect(commandTask).toHaveBeenCalledWith('root_a', 'configure_workflow', 4, expect.objectContaining({ orchestration: expect.any(Object) }), expect.any(String)));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('草稿已保留');
    expect(within(dialog).getAllByLabelText('步骤名称')[0]).toHaveValue('保留我的调查草稿');
  });
  it('审批冲突保留反馈，新候选不沿用旧版本批准', async () => {
    const detail = taskDetail(); detail.task.paused = false; detail.task.status = 'waiting';
    detail.task.candidate = { id: 'ws_before', base_sha: 'base' };
    detail.board.decisions = [{ id: 'decision', task_id: detail.task.id, kind: 'release', question: '批准上线吗？', options: [{ id: 'approve', text: '批准此版本' }], status: 'pending', contract_id: 'agreement', candidate_id: 'ws_before', artifact_paths: [] }];
    vi.mocked(fetchTask).mockResolvedValue(detail); vi.mocked(commandTask).mockRejectedValue(new Error('候选版本已变化'));
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    fireEvent.click(await screen.findByRole('button', { name: /查看并决定/ }));
    const dialog = screen.getByRole('dialog', { name: '上线审批' });
    fireEvent.change(within(dialog).getByLabelText('反馈或修改意见（可选）'), { target: { value: '先检查目标环境' } });
    const changed = structuredClone(detail); changed.board.revision++; changed.task.candidate = { id: 'ws_after', base_sha: 'base' }; changed.board.tasks[0] = changed.task;
    vi.mocked(fetchTask).mockResolvedValue(changed);
    fireEvent.click(within(dialog).getByRole('button', { name: '批准此版本' }));
    expect(await within(dialog).findByText('此决定对应的版本已失效，请等待新的决定。')).toBeVisible();
    expect(within(dialog).getByLabelText('反馈或修改意见（可选）')).toHaveValue('先检查目标环境');
    expect(within(dialog).queryByRole('button', { name: '批准此版本' })).not.toBeInTheDocument();
  });
});
describe('workflow配置与展示', () => {
  it('详情指定的根任务不被其他根任务或决定自动替换', () => {
    const detail = taskDetail();
    const other = { ...structuredClone(detail.task), id: 'other_root', root_id: 'other_root', title: '其他任务', status: 'running' as const };
    detail.board.tasks.unshift(other);
    detail.board.decisions = [{ id: 'other_decision', task_id: other.id, kind: 'design', question: '其他任务的决定', options: [], status: 'pending', contract_id: 'agreement', artifact_paths: [] }];
    render(<AdaptiveWorkBody taskId="root_a" board={detail.board} conversationId="execution_root_a" goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} requestedDecision="other_decision" />);
    expect(screen.getByRole('heading', { level: 2, name: '任务 root_a' })).toBeInTheDocument();
    expect(screen.queryByLabelText('当前任务')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('拒绝循环依赖，表单失败保留目标与步骤', async () => {
    const detail = taskDetail(); detail.orchestration.steps[0].depends_on = ['implement'];
    expect(workflowError(detail.orchestration)).toContain('循环');
    const save = vi.fn().mockRejectedValue(new Error('连接中断，稍后重试'));
    render(<NewTaskDialog agents={agents} onClose={vi.fn()} onSave={save} />);
    fireEvent.change(screen.getByLabelText('任务名称'), { target: { value: '保留这份任务' } });
    fireEvent.change(screen.getByLabelText('要交付什么'), { target: { value: '交付目标' } });
    fireEvent.change(screen.getByLabelText('怎样算完成（每行一项）'), { target: { value: '可验证' } });
    fireEvent.click(screen.getByRole('button', { name: '保存任务' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断');
    expect(screen.getByLabelText('任务名称')).toHaveValue('保留这份任务');
    expect(within(screen.getByRole('navigation', { name: '流程步骤' })).getAllByRole('button')).toHaveLength(4);
  });
  it('对话框先打开、伙伴稍后加载时补全空负责人，保留目标和模板', async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<NewTaskDialog agents={[]} onClose={vi.fn()} onSave={save} />);
    fireEvent.change(screen.getByLabelText('任务名称'), { target: { value: '异步伙伴任务' } });
    fireEvent.change(screen.getByLabelText('要交付什么'), { target: { value: '保留已经填写的目标' } });
    fireEvent.change(screen.getByLabelText('怎样算完成（每行一项）'), { target: { value: '能够核对结果' } });
    rerender(<NewTaskDialog agents={agents} onClose={vi.fn()} onSave={save} />);
    await waitFor(() => expect(screen.getAllByLabelText('步骤负责人').every(select => (select as HTMLSelectElement).value === 'chulan')).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: '保存任务' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    const payload = save.mock.calls[0][0];
    expect(payload.owner_id).toBe('chulan'); expect(payload.paused).toBe(true);
    expect(payload.agreement.goal).toBe('保留已经填写的目标');
    expect(payload.orchestration.steps.every((step: { owner_id: string }) => step.owner_id === 'chulan')).toBe(true);
  });
  it('调整编排仅提交逻辑步骤，不回传执行任务绑定', async () => {
    const detail = taskDetail(); vi.mocked(commandTask).mockResolvedValue(detail);
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    fireEvent.click(await screen.findByRole('button', { name: '调整编排' }));
    fireEvent.click(screen.getByRole('button', { name: '保存编排' }));
    await waitFor(() => expect(commandTask).toHaveBeenCalledOnce());
    const submitted = vi.mocked(commandTask).mock.calls[0][3] as { orchestration: { steps: Record<string, unknown>[] } };
    expect(submitted.orchestration.steps.every(step => !('task_id' in step))).toBe(true);
    expect(submitted.orchestration.steps[0].id).toBe('investigate');
  });
  it.each([true, false])('仅有可执行步骤时显示执行通道排队，等待用户不会误称排队：%s', readyStep => {
    const detail = taskDetail(); detail.task.paused = false; detail.task.status = 'waiting';
    detail.board.execution = { active: [], queued: true };
    if (readyStep) detail.board.tasks.push({ ...structuredClone(detail.task), id: 'child_ready', parent_id: detail.task.id, root_id: detail.task.id, status: 'ready', paused: false });
    render(<AdaptiveWorkBody embedded taskId={detail.task.id} board={detail.board} conversationId={detail.execution_conversation_id} goal="" agents={agents} busy={false} error="" archived={false} command={vi.fn()} />);
    if (readyStep) expect(screen.getByText('排队中：执行通道正在处理其他工作，已就绪步骤会在空闲后继续。')).toBeVisible();
    else expect(screen.queryByText(/排队中/)).not.toBeInTheDocument();
  });
  it('合法步骤名__root__不与整体责任节点共用图形ID', () => {
    const detail = taskDetail();
    detail.orchestration.steps = [{ id: '__root__', title: '允许的步骤名称', owner_id: 'chulan', goal: '完成目标', depends_on: [] }];
    render(<WorkflowGraph detail={detail} agents={agents} />);
    expect(screen.getByRole('button', { name: '允许的步骤名称，尚未开始' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '整体交付与验收，已暂停' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '允许的步骤名称，尚未开始' }).style.left).not.toBe('');
  });
  it('预设流程展示新委派的真实协作，以及当前实际等待关系', () => {
    const detail = taskDetail(); detail.task.paused = false; detail.task.depends_on = ['step_b'];
    detail.orchestration.steps[1].task_id = 'step_b';
    const base = { ...structuredClone(detail.task), root_id: detail.task.id, paused: false };
    detail.board.tasks.push({ ...base, id: 'step_a', parent_id: detail.task.id, workflow_step_id: 'investigate', status: 'waiting', depends_on: ['helper'] },
      { ...base, id: 'step_b', parent_id: detail.task.id, workflow_step_id: 'implement', status: 'ready', depends_on: ['step_a'] },
      { ...base, id: 'helper', parent_id: 'step_a', title: '补查接口', status: 'ready', depends_on: [] });
    render(<WorkflowGraph detail={detail} agents={agents} />);
    const collaborator = screen.getByRole('button', { name: '补查接口，等待执行' });
    expect(within(collaborator).getByText('新增协作')).toBeVisible();
    expect(within(screen.getByRole('button', { name: '调查现状，等待处理' })).getByText('等待：补查接口')).toBeVisible();
    expect(within(screen.getByRole('button', { name: '整体交付与验收，等待执行' })).getByText('等待：按方案实施')).toBeVisible();
  });
  it('节点状态来自匹配执行记录，未生成任务的步骤不会伪装成完成', () => {
    const detail = taskDetail();
    detail.board.tasks.push({ ...structuredClone(detail.task), id: 'step_a', parent_id: 'root_a', root_id: 'root_a', workflow_step_id: 'investigate', status: 'done', paused: false, progress: '已核对输入' });
    render(<WorkflowGraph detail={detail} agents={agents} />);
    expect(screen.getByRole('button', { name: '调查现状，已交付' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '按方案实施，尚未开始' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '调查现状，已交付' }));
    expect(within(screen.getByLabelText('步骤详情')).getByText('已核对输入')).toBeVisible();
  });
  it('步骤详情点击后用弹窗展示，关闭后恢复焦点且不占用流程下方空间', () => {
    render(<WorkflowGraph detail={taskDetail()} agents={agents} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: '调查现状，尚未开始' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog');
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole('dialog', { name: '调查现状' });
    expect(within(dialog).getByText('核对输入与现有行为')).toBeVisible();
    expect(within(screen.getByRole('region', { name: '任务工作流' })).queryByLabelText('步骤详情')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭调查现状' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '按方案实施，尚未开始' }));
    expect(within(screen.getByRole('dialog', { name: '按方案实施' })).getByText('实现交付目标')).toBeVisible();
  });
  it('固定流程不展示后端不允许的需求改写入口', async () => {
    render(<TasksWorkspace selectedTaskId="root_a" onSelectTask={vi.fn()} onOpenConversation={vi.fn()} agents={agents} />);
    await screen.findByRole('heading', { level: 1, name: '任务 root_a' });
    expect(screen.queryByRole('button', { name: '调整需求' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '调整编排' })).toBeEnabled();
  });
  it('窄屏节点顺序尊重依赖，独立分支之间不画虚构的相邻箭头', () => {
    const detail = taskDetail();
    detail.orchestration.steps = [
      { id: 'later', title: '后置步骤', owner_id: 'chulan', goal: '依赖先行结果', depends_on: ['early'] },
      { id: 'early', title: '先行步骤', owner_id: 'chulan', goal: '先准备材料', depends_on: [] },
      { id: 'independent', title: '独立分支', owner_id: 'baobao', goal: '另行核对', depends_on: [] },
    ];
    render(<WorkflowGraph detail={detail} agents={agents} />);
    const graph = screen.getByRole('region', { name: '任务工作流' });
    const buttons = within(graph).getAllByRole('button');
    expect(buttons.indexOf(screen.getByRole('button', { name: '先行步骤，尚未开始' }))).toBeLessThan(buttons.indexOf(screen.getByRole('button', { name: '后置步骤，尚未开始' })));
    expect(screen.getByRole('button', { name: '先行步骤，尚未开始' }).querySelector('.task-mobile-arrow')).toBeNull();
    expect(screen.getByRole('button', { name: '独立分支，尚未开始' }).querySelector('.task-mobile-arrow')).toBeNull();
  });

});
