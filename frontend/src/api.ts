import type { Task, TaskSummary } from "./types";
import type { CopyrightState, Operation, SourcePreview, Stage } from "./copyright";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : "输入内容不符合要求，请检查必填项");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (value: unknown): RequestInit => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

export const api = {
  listTasks: () => request<TaskSummary[]>("/api/tasks"),
  getTask: (id: string) => request<Task>(`/api/tasks/${id}`),
  createTask: (data: FormData) => request<Task>("/api/tasks", { method: "POST", body: data }),
  analyze: (id: string) => request<Task>(`/api/tasks/${id}/analyze`, { method: "POST" }),
  review: (id: string, data: unknown) => request<Task>(`/api/tasks/${id}/review`, json(data)),
  credentials: (id: string, data: unknown) => request<void>(`/api/tasks/${id}/credentials`, json(data)),
  action: (id: string, action: string) => request(`/api/tasks/${id}/${action}`, { method: "POST" }),
  approval: (id: string, approvalId: string, approved: boolean) => request(`/api/tasks/${id}/approvals/${approvalId}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approved }) }),
  screenshot: (id: string, screenshotId: string, included: boolean) => request(`/api/tasks/${id}/screenshots/${screenshotId}`, json({ included })),
  report: (id: string) => request<Task>(`/api/tasks/${id}/report`, { method: "POST" }),
  remove: (id: string) => request<void>(`/api/tasks/${id}`, { method: "DELETE" }),
  copyright: (id: string) => request<CopyrightState>(`/api/tasks/${id}/copyright`),
  copyrightSave: (id: string, stage: Stage, revision: number, value: unknown) => request<CopyrightState>(`/api/tasks/${id}/copyright/stages/${stage}`, json({ revision, value })),
  copyrightConfirm: (id: string, stage: Stage, revision: number) => request<CopyrightState>(`/api/tasks/${id}/copyright/stages/${stage}/confirm`, { ...json({ revision }), method: "POST" }),
  copyrightGenerate: (id: string, operation: Operation, revision: number) => request(`/api/tasks/${id}/copyright/generate/${operation}`, { ...json({ revision }), method: "POST" }),
  copyrightSource: (id: string) => request<SourcePreview>(`/api/tasks/${id}/copyright/source-preview`),
  copyrightResolve: (id: string, revision: number, issue: string, note: string) => request<CopyrightState>(`/api/tasks/${id}/copyright/review-resolutions`, { ...json({ revision, issue, note }), method: "POST" }),
};
