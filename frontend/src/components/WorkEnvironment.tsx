import type { SharedWorkTask } from '../types';
import { ExternalLink, LoaderCircle, Monitor, Play, Square } from 'lucide-react';

const states: Record<string, string> = {
  pending: '正在准备', preparing: '正在准备', ready: '已就绪', failed: '准备失败',
};
const previews: Record<string, string> = {
  stopped: '未启动', starting: '正在准备预览', ready: '运行中', failed: '启动失败',
};

function localUrl(value?: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function WorkEnvironment({ task, conversationId, busy, command }: {
  task: SharedWorkTask; conversationId: string; busy: boolean;
  command: (action: string, data?: Record<string, unknown>) => Promise<boolean>;
}) {
  const env = task.environment;
  if (!env) return null;
  const preview = env.preview;
  const url = localUrl(preview?.url);
  const running = preview?.status === 'ready';
  const starting = preview?.status === 'starting';
  const deferred = task.paused && !task.active_mission_id && !(task.attempts_used || task.attempts?.length) && env.status === 'pending';
  const canStart = task.paused || ['waiting', 'done'].includes(task.status);
  return <section className="work-section work-environment" aria-label="任务工作区与预览">
    <div className="work-section-heading"><h3>工作区与预览</h3><span className="work-muted">{deferred ? '开始执行后准备' : states[env.status] || env.status}</span></div>
    {env.failure_reason && <p className="work-error" role="status">{env.failure_reason}</p>}
    {env.status === 'failed' && task.status !== 'cancelled' && <button className="work-primary" disabled={busy} onClick={() => void command('retry_environment', { task_id: task.id })}>重试准备工作区</button>}
    {env.status === 'ready' && <>
      <p className="work-muted">本任务使用独立代码目录，协作和验证沿用同一工作区。</p>
      <div className="work-preview-actions">
        <div className="work-preview-state" data-state={preview?.status || 'stopped'}><Monitor size={17} aria-hidden="true" /><span>预览</span><strong>{previews[preview?.status || 'stopped'] || preview?.status}</strong></div>
        <div className="work-preview-buttons">
          {running && url && <a className="work-primary" href={url} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden="true" />打开预览</a>}
          {running ? <button className="work-preview-stop" disabled={busy} onClick={() => void command('stop_preview', { task_id: task.id })}><Square size={13} aria-hidden="true" />停止预览</button>
            : <button className="work-primary" disabled={busy || starting || !!task.active_mission_id || !canStart || task.status === 'cancelled'} onClick={() => void command('start_preview', { task_id: task.id })}>{starting ? <LoaderCircle className="work-preview-loading" size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}{starting ? '正在准备预览…' : '启动预览'}</button>}
        </div>
      </div>
      {!running && !starting && !canStart && task.status !== 'cancelled' && <p className="work-muted">可暂停任务，或等到待验收时启动预览。</p>}
      {preview?.failure_reason && <p className="work-error" role="status">{preview.failure_reason}</p>}
      {preview?.log_id && <a href={`/api/conversations/${encodeURIComponent(conversationId)}/work/logs/${encodeURIComponent(preview.log_id)}`} target="_blank" rel="noreferrer">查看预览日志</a>}
      <details className="work-file-source"><summary>工作区版本依据</summary><dl><dt>分支</dt><dd>{env.branch}</dd><dt>创建基线</dt><dd>{env.base_sha}</dd>{preview?.candidate_id && <><dt>预览对应候选</dt><dd>{preview.candidate_id}</dd></>}</dl></details>
    </>}
  </section>;
}
