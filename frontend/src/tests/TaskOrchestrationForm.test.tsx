import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConfigureWorkflowDialog } from '../components/NewTaskDialog';
import { TaskOrchestrationForm, taskWorkflowTemplate } from '../components/TaskOrchestrationForm';

const agents = [{ id: 'chulan', display_name: '张楚岚' }, { id: 'baobao', display_name: '冯宝宝' }];
function Editor() {
  const [value, setValue] = useState(taskWorkflowTemplate('chulan'));
  return <TaskOrchestrationForm value={value} onChange={setValue} ownerId="chulan" agents={agents} />;
}

describe('编排步骤编辑器', () => {
  it('切换步骤保留各自名称、目标与负责人', () => {
    render(<Editor />);
    fireEvent.change(screen.getByLabelText('步骤名称'), { target: { value: '需求调研' } });
    fireEvent.click(screen.getByRole('button', { name: /02 实施/ }));
    fireEvent.change(screen.getByLabelText('步骤目标'), { target: { value: '实现报名与确认页面' } });
    fireEvent.change(screen.getByLabelText('步骤负责人'), { target: { value: 'baobao' } });
    fireEvent.click(screen.getByRole('button', { name: /01 需求调研/ }));
    expect(screen.getByLabelText('步骤名称')).toHaveValue('需求调研');
    fireEvent.click(screen.getByRole('button', { name: /02 实施/ }));
    expect(screen.getByLabelText('步骤目标')).toHaveValue('实现报名与确认页面');
    expect(screen.getByLabelText('步骤负责人')).toHaveValue('baobao');
    expect(screen.getByRole('button', { name: /02 实施/ })).toHaveAttribute('aria-current', 'step');
  });

  it('往返切换自主编排不会覆盖已编辑的指定流程', () => {
    render(<Editor />);
    fireEvent.change(screen.getByLabelText('步骤名称'), { target: { value: '保留我的方案' } });
    fireEvent.click(screen.getByRole('radio', { name: /自主编排/ }));
    expect(screen.queryByLabelText('步骤名称')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /指定流程/ }));
    expect(screen.getByLabelText('步骤名称')).toHaveValue('保留我的方案');
    expect(within(screen.getByRole('navigation', { name: '流程步骤' })).getAllByRole('button')).toHaveLength(4);
  });

  it('新增后直接编辑新步骤；删除步骤时清除其他步骤的引用', async () => {
    const save = vi.fn().mockResolvedValue(true);
    render(<ConfigureWorkflowDialog initial={taskWorkflowTemplate('chulan')} agents={agents} ownerId="chulan" onClose={vi.fn()} onSave={save} />);
    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }));
    expect(screen.getByLabelText('步骤名称')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: '验证与交付' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /02 实施/ }));
    fireEvent.click(screen.getByRole('button', { name: '删除步骤2' }));
    expect(screen.getByLabelText('步骤名称')).toHaveValue('验证与交付');
    expect(screen.queryByRole('checkbox', { name: '实施' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /03 未命名步骤/ }));
    fireEvent.change(screen.getByLabelText('步骤名称'), { target: { value: '整理文档' } });
    fireEvent.change(screen.getByLabelText('步骤目标'), { target: { value: '记录使用说明' } });
    fireEvent.click(screen.getByRole('button', { name: '保存编排' }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    const submitted = save.mock.calls[0][0];
    expect(submitted.steps.map((step: { id: string }) => step.id)).not.toContain('implement');
    expect(submitted.steps.find((step: { id: string }) => step.id === 'verify').depends_on).toEqual([]);
    expect(submitted.steps[2]).toMatchObject({ title: '整理文档', goal: '记录使用说明', depends_on: ['verify'] });
  });

  it('未选中的步骤未填写完整时也不能保存，草稿仍保留', () => {
    const save = vi.fn();
    render(<ConfigureWorkflowDialog initial={taskWorkflowTemplate('chulan')} agents={agents} ownerId="chulan" onClose={vi.fn()} onSave={save} />);
    fireEvent.click(screen.getByRole('button', { name: '添加步骤' }));
    fireEvent.change(screen.getByLabelText('步骤名称'), { target: { value: '稍后补目标' } });
    fireEvent.click(screen.getByRole('button', { name: /01 调查与方案/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存编排' }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('请填写每个步骤');
    fireEvent.click(screen.getByRole('button', { name: /04 稍后补目标/ }));
    expect(screen.getByLabelText('步骤名称')).toHaveValue('稍后补目标');
  });
});
