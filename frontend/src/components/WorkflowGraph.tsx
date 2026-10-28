import { useState, type CSSProperties } from 'react';
import { ArrowDown, Check, Clock3, GitBranch, LoaderCircle, Pause } from 'lucide-react';
import type { SharedWorkTask, TaskDetail } from '../types';
import { taskStatus } from '../lib/workPresentation';
import { WorkArtifacts } from './WorkArtifacts';
import { WorkDialog } from './WorkDialog';
import type { TaskAgent } from './TaskOrchestrationForm';

interface FlowNode { id: string; title: string; owner: string; goal: string; dependencies: string[]; task?: SharedWorkTask; root?: boolean; collaboration?: boolean }
const NODE_WIDTH = 200;
function flowNodes(detail: TaskDetail): FlowNode[] {
  const root = detail.task, rootNodeId = `root:${detail.task.id}`;
  const related = detail.board.tasks.filter(t => t.id !== root.id && (t.root_id === root.id || t.parent_id === root.id));
  const planned: FlowNode[] = detail.orchestration.mode === 'workflow' ? detail.orchestration.steps.map(step => ({
    id: step.id, title: step.title, owner: step.owner_id, goal: step.goal, dependencies: step.depends_on,
    task: related.find(t => t.id === step.task_id || t.workflow_step_id === step.id),
  })) : [];
  const bound = new Set(planned.flatMap(n => n.task ? [n.task.id] : []));
  const collaborators: FlowNode[] = related.filter(t => !bound.has(t.id)).map(t => ({
    id: t.id, title: t.title, owner: t.owner_id, goal: t.next_action || t.title,
    dependencies: t.depends_on, task: t, collaboration: true,
  }));
  const nodes: FlowNode[] = [...planned, ...collaborators, { id: rootNodeId, title: detail.orchestration.mode === 'workflow' ? '整体交付与验收' : '整体责任',
    owner: root.owner_id, goal: root.title, dependencies: root.depends_on, task: root, root: true }];
  const taskToNode = new Map(nodes.flatMap(n => n.task ? [[n.task.id, n.id] as const] : []));
  const visible = new Set(nodes.map(n => n.id));
  return nodes.map(node => ({ ...node,
    // A configured step is a planned dependency until its execution record exists.
    // Once bound, actual persistent dependencies include any newly delegated work.
    dependencies: (node.task ? node.task.depends_on : node.dependencies)
      .map(id => taskToNode.get(id) || id).filter(id => visible.has(id) && id !== node.id),
  }));
}
function layout(nodes: FlowNode[]) {
  const levels = new Map<string, number>(), visiting = new Set<string>();
  const rank = (id: string): number => {
    if (levels.has(id)) return levels.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const node = nodes.find(n => n.id === id);
    const level = node?.dependencies.length ? 1 + Math.max(...node.dependencies.map(parent => nodes.some(n => n.id === parent) ? rank(parent) : -1)) : 0;
    visiting.delete(id); levels.set(id, level); return level;
  };
  const rows = new Map<number, number>();
  const positions = new Map(nodes.map(node => { const column = rank(node.id), row = rows.get(column) || 0; rows.set(column, row + 1); return [node.id, { x: 16 + column * (NODE_WIDTH + 28), y: 16 + row * 112 }]; }));
  return { positions, width: Math.max(250, ...[...positions.values()].map(p => p.x + NODE_WIDTH + 16)), height: Math.max(134, ...[...positions.values()].map(p => p.y + 112)) };
}
export function WorkflowGraph({ detail, agents }: { detail: TaskDetail; agents: TaskAgent[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  const nodes = flowNodes(detail), graph = layout(nodes);
  const ordered = [...nodes].sort((a, b) => { const x = graph.positions.get(a.id)!, y = graph.positions.get(b.id)!; return x.x - y.x || x.y - y.y; });
  const active = nodes.find(n => n.id === selected);
  const label = (id: string) => agents.find(a => a.id === id)?.display_name || id;
  return <section className="flow-section" aria-label="任务工作流">
    <div className="task-flow-heading"><div><h2><GitBranch size={18} />{detail.orchestration.mode === 'workflow' ? 'Workflow' : '自主编排'}</h2><p>{detail.orchestration.mode === 'workflow' ? '按依赖推进，点击步骤查看实际进展。' : '负责人按需安排动作，协作出现后会在这里展开。'}</p></div><span className="task-flow-legend"><i />运行中 <i />待处理 <i />已完成</span></div>
    <div className="task-flow-scroll"><div className="task-flow-canvas" style={{ width: graph.width, height: graph.height, '--flow-node-width': `${NODE_WIDTH}px` } as CSSProperties}>
      <svg className="task-flow-edges" width={graph.width} height={graph.height} aria-hidden="true"><defs><marker id={`flow-arrow-${detail.task.id}`} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="currentColor" /></marker></defs>{nodes.flatMap(node => node.dependencies.map(dependency => {
        const start = graph.positions.get(dependency), end = graph.positions.get(node.id); if (!start || !end) return null;
        const x = start.x + NODE_WIDTH, y = start.y + 44, targetX = end.x - 6, targetY = end.y + 44, mid = (x + targetX) / 2;
        return <path key={`${dependency}-${node.id}`} d={`M ${x} ${y} C ${mid} ${y}, ${mid} ${targetY}, ${targetX} ${targetY}`} markerEnd={`url(#flow-arrow-${detail.task.id})`} />;
      }))}</svg>
      {ordered.map((node, index) => { const p = graph.positions.get(node.id)!, state = node.task ? taskStatus(node.task, detail.board) : '尚未开始'; const status = node.task?.paused ? 'paused' : node.task?.status || 'pending';
        return <button key={node.id} type="button" className={`task-flow-node flow-node-${status}${node.root ? ' is-root' : ''}${selected === node.id ? ' is-selected' : ''}`} style={{ left: p.x, top: p.y }} onClick={() => setSelected(node.id)} aria-haspopup="dialog" aria-label={`${node.title}，${state}`}>
          {node.collaboration && <span className="task-node-kind">新增协作</span>}<span className="task-node-state">{status === 'done' ? <Check size={14} /> : status === 'paused' ? <Pause size={13} /> : status === 'running' ? <LoaderCircle size={14} /> : <Clock3 size={13} />}{state}</span><strong>{node.title}</strong><span className="task-node-owner">{label(node.owner)}{node.root ? ' · 整体负责' : ''}</span>
          <span className="task-node-deps">{node.dependencies.length ? `等待：${node.dependencies.map(id => nodes.find(n => n.id === id)?.title || id).join('、')}` : '无前置依赖'}</span>{ordered[index + 1]?.dependencies.includes(node.id) && <ArrowDown className="task-mobile-arrow" size={16} />}
        </button>;
      })}
    </div></div>
    {active && <WorkDialog title={active.title} className="flow-detail-dialog" onClose={() => setSelected(null)}><section className="task-node-detail" aria-label="步骤详情"><p>{active.goal}</p><dl><dt>负责人</dt><dd>{label(active.owner)}</dd><dt>当前状态</dt><dd>{active.task ? taskStatus(active.task, detail.board) : '尚未开始'}</dd><dt>前置步骤</dt><dd>{active.dependencies.map(id => nodes.find(n => n.id === id)?.title || id).join('、') || '无'}</dd></dl>{active.task && <><p className="work-prose">{active.task.blocker || active.task.next_action || active.task.progress || '等待记录进展。'}</p><p className="work-muted">{active.task.attempts.length} 次执行 · {active.task.evidence.length} 份验证记录 · {active.task.reviews.length} 份审核记录</p>{active.task.submission && <WorkArtifacts conversationId={detail.execution_conversation_id} version={active.task.candidate?.id} paths={active.task.submission.artifact_paths} />}</>}</section></WorkDialog>}
  </section>;
}
