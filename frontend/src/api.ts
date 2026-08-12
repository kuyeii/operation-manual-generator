import type { Task, TaskSummary } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(body.detail || "请求失败");
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
};
