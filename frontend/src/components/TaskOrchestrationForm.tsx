import { useId, useRef, useState } from 'react';
import { ArrowRight, Check, GitBranch, Plus, RotateCcw, Sparkles, Trash2 } from 'lucide-react';
import './TaskOrchestrationForm.css';
import type { TaskOrchestration } from '../types';
export interface TaskAgent { id: string; display_name: string }
export function taskWorkflowTemplate(owner: string): TaskOrchestration {
  return { mode: 'workflow', steps: [
    { id: 'investigate', title: '调查与方案', owner_id: owner, goal: '调查现状，形成实施方案和需要保留的行为。', depends_on: [] },
    { id: 'implement', title: '实施', owner_id: owner, goal: '按目标和验收条件实施，保留结果与限制。', depends_on: ['investigate'] },
    { id: 'verify', title: '验证与交付', owner_id: owner, goal: '核对交付结果，运行适用检查并记录证据。', depends_on: ['implement'] },
  ] };
}
export function workflowError(value: TaskOrchestration): string {
  if (value.mode === 'adaptive') return '';
  if (!value.steps.length) return '请添加至少一个流程步骤。';
  const ids = new Set(value.steps.map(step => step.id));
  if (ids.size !== value.steps.length) return '步骤编号重复，请重新添加该步骤。';
  if (value.steps.some(s => !s.title.trim() || !s.owner_id || !s.goal.trim())) return '请填写每个步骤的名称、负责人和目标。';
  if (value.steps.some(s => s.depends_on.some(id => !ids.has(id)))) return '存在已删除的依赖步骤，请重新选择。';
  const visited = new Set<string>(), visiting = new Set<string>();
  const cycle = (id: string): boolean => {
    if (visiting.has(id)) return true;
    if (visited.has(id)) return false;
    visiting.add(id);
    if (value.steps.find(s => s.id === id)!.depends_on.some(cycle)) return true;
    visiting.delete(id); visited.add(id); return false;
  };
  return value.steps.some(s => cycle(s.id)) ? '步骤依赖形成了循环，请调整依赖。' : '';
}
export function TaskOrchestrationForm({ value, onChange, agents, ownerId, disabled = false }: {
  value: TaskOrchestration; onChange: (value: TaskOrchestration) => void; agents: TaskAgent[]; ownerId: string; disabled?: boolean;
}) {
  const [selectedId, setSelectedId] = useState(value.steps[0]?.id || '');
  const cachedWorkflow = useRef<TaskOrchestration>(value.mode === 'workflow' ? value : taskWorkflowTemplate(ownerId));
  const modeName = useId();
  const activeStep = value.steps.find(step => step.id === selectedId) || value.steps[0];
  const activeIndex = value.steps.findIndex(step => step.id === activeStep?.id);
  const updateValue = (next: TaskOrchestration) => {
    if (next.mode === 'workflow') cachedWorkflow.current = next;
    onChange(next);
  };
  const update = (id: string, changes: Partial<TaskOrchestration['steps'][number]>) => updateValue({ ...value, steps: value.steps.map(s => s.id === id ? { ...s, ...changes } : s) });
  const addStep = () => {
    const id = `step_${globalThis.crypto?.randomUUID?.() || Date.now().toString(36)}`;
    updateValue({ ...value, steps: [...value.steps, { id, title: '', owner_id: ownerId, goal: '', depends_on: value.steps.length ? [value.steps[value.steps.length - 1].id] : [] }] });
    setSelectedId(id);
  };
  const removeStep = (id: string) => {
    const remaining = value.steps.filter(step => step.id !== id).map(step => ({ ...step, depends_on: step.depends_on.filter(dependency => dependency !== id) }));
    updateValue({ ...value, steps: remaining });
    setSelectedId(remaining[Math.min(Math.max(activeIndex, 0), remaining.length - 1)]?.id || '');
  };
  const chooseMode = (mode: TaskOrchestration['mode']) => {
    if (mode === value.mode) return;
    if (mode === 'adaptive') {
      cachedWorkflow.current = value;
      onChange({ mode, steps: [] });
    } else {
      const restored = cachedWorkflow.current;
      updateValue({ ...restored, steps: restored.steps.map(step => ({ ...step, owner_id: step.owner_id || ownerId })) });
    }
  };
  const ownerLabel = (id: string) => agents.find(agent => agent.id === id)?.display_name || '未指定负责人';
  return <fieldset className="flow-config-form" disabled={disabled}>
    <legend>编排方式</legend>
    <div className="flow-mode-grid">
      {([{ mode: 'adaptive', title: '自主编排', description: '由负责人安排下一步，按需发起协作。', icon: Sparkles },
        { mode: 'workflow', title: '指定流程', description: '定义步骤、负责人和依赖，按流程推进。', icon: GitBranch }] as const).map(({ mode, title, description, icon: Icon }) =>
        <label className={`flow-mode-card${value.mode === mode ? ' is-selected' : ''}`} key={mode}>
          <input className="flow-mode-input" type="radio" name={modeName} value={mode} checked={value.mode === mode} onChange={() => chooseMode(mode)} />
          <span className="flow-mode-icon"><Icon size={19} aria-hidden="true" /></span>
          <span className="flow-mode-copy"><strong>{title}</strong><small>{description}</small></span>
          <span className="flow-mode-check" aria-hidden="true">{value.mode === mode && <Check size={13} />}</span>
        </label>)}
    </div>
    {value.mode === 'adaptive' ? <div className="flow-auto-note"><Sparkles size={18} aria-hidden="true" /><div><strong>从目标出发，动态安排协作</strong><p>无需预设步骤。执行产生的协作与进展仍会显示在任务工作流中。</p></div></div> : <>
      <div className="flow-editor-toolbar"><div><strong>流程步骤</strong><span>{value.steps.length} 个步骤 · {value.steps.reduce((count, step) => count + step.depends_on.length, 0)} 条依赖</span></div><button type="button" className="flow-reset" onClick={() => {
        const template = taskWorkflowTemplate(ownerId); updateValue(template); setSelectedId(template.steps[0].id);
      }}><RotateCcw size={13} aria-hidden="true" />恢复默认模板</button></div>
      <div className="flow-editor-layout">
        <nav className="flow-step-nav" aria-label="流程步骤">
          <ol>{value.steps.map((step, index) => <li key={step.id}>
            <button type="button" className={`flow-step-link${activeStep?.id === step.id ? ' is-active' : ''}`} aria-current={activeStep?.id === step.id ? 'step' : undefined} onClick={() => setSelectedId(step.id)}>
              <span className="flow-step-index">{String(index + 1).padStart(2, '0')}</span><span className="flow-step-copy"><strong>{step.title || '未命名步骤'}</strong><small>{ownerLabel(step.owner_id)}</small></span>
              {(!step.title.trim() || !step.goal.trim() || !step.owner_id) && <span className="flow-incomplete" aria-label="待完善" />}
            </button>
          </li>)}</ol>
          <button className="flow-add" type="button" onClick={addStep}><Plus size={15} aria-hidden="true" />添加步骤</button>
        </nav>
        {activeStep ? <section className="flow-step-panel" aria-label="编辑当前步骤" key={activeStep.id}>
          <div className="flow-panel-header"><div><span>步骤 {String(activeIndex + 1).padStart(2, '0')}</span><h3>{activeStep.title || '新步骤'}</h3></div><button type="button" className="flow-delete" aria-label={`删除步骤${activeIndex + 1}`} onClick={() => removeStep(activeStep.id)}><Trash2 size={15} aria-hidden="true" /><span>删除</span></button></div>
          <div className="flow-fields"><label className="flow-field">步骤名称<input required maxLength={200} value={activeStep.title} placeholder="例如：调查现状" onChange={e => update(activeStep.id, { title: e.target.value })} /></label><label className="flow-field">步骤负责人<select required value={activeStep.owner_id} onChange={e => update(activeStep.id, { owner_id: e.target.value })}>{!activeStep.owner_id && <option value="">选择负责人</option>}{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.display_name}</option>)}</select></label></div>
          <label className="flow-field">步骤目标<textarea required rows={3} value={activeStep.goal} placeholder="描述这一阶段应完成的工作与结果" onChange={e => update(activeStep.id, { goal: e.target.value })} /></label>
          <fieldset className="flow-dependencies"><legend>前置依赖 <span>可多选</span></legend><p>选中的步骤完成后，当前步骤才会开始。</p><div>{value.steps.filter(step => step.id !== activeStep.id).map(other => <label key={other.id} className={activeStep.depends_on.includes(other.id) ? 'is-checked' : ''}><input type="checkbox" checked={activeStep.depends_on.includes(other.id)} onChange={e => update(activeStep.id, { depends_on: e.target.checked ? [...activeStep.depends_on, other.id] : activeStep.depends_on.filter(id => id !== other.id) })} /><span>{other.title || '未命名步骤'}</span></label>)}</div>{!activeStep.depends_on.length && <small><ArrowRight size={12} aria-hidden="true" />无前置依赖，开始任务后即可推进。</small>}</fieldset>
        </section> : <div className="flow-empty"><GitBranch size={24} aria-hidden="true" /><strong>添加第一个步骤</strong><p>先定义要做什么，再分配负责人和前置依赖。</p><button type="button" className="task-secondary" onClick={addStep}><Plus size={14} />添加步骤</button></div>}
      </div>
      <p className="flow-editor-note">依赖关系决定执行顺序；独立审核仍按项目风险要求执行。</p>
    </>}
  </fieldset>;
}
