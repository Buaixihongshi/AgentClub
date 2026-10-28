import { newRequestId } from './api';
import type { TaskDetail, TaskOrchestration, TaskSummary, WorkContract } from '../types';

export interface NewTaskPayload {
  title: string;
  owner_id: string;
  agreement: Omit<WorkContract, 'id' | 'version' | 'digest' | 'activated_at'>;
  orchestration: TaskOrchestration;
  paused: true;
  source_conversation_id?: string;
  operation_id?: string;
}
async function read<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let message = fallback;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg?: string }) => item.msg || '').filter(Boolean).join('；') || fallback;
    } catch { /* Preserve the useful operation label for non-JSON failures. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
export async function listTasks(): Promise<TaskSummary[]> {
  const result = await read<{ tasks: TaskSummary[] }>(await fetch('/api/tasks'), '加载任务列表失败');
  return result.tasks;
}
export async function fetchTask(id: string): Promise<TaskDetail> {
  return read(await fetch(`/api/tasks/${encodeURIComponent(id)}`), '加载任务详情失败');
}
export async function createTask(payload: NewTaskPayload): Promise<TaskDetail> {
  return read(await fetch('/api/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, paused: true, operation_id: payload.operation_id || newRequestId('create-task') }),
  }), '保存任务失败');
}
export async function commandTask(id: string, action: string, revision: number, data: Record<string, unknown> = {}, operationId = newRequestId('task-command')): Promise<TaskDetail> {
  return read(await fetch(`/api/tasks/${encodeURIComponent(id)}/commands`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, expected_revision: revision, data, operation_id: operationId }),
  }), '更新任务失败');
}
