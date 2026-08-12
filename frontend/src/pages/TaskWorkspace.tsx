import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, CircleAlert, Download, Eye, EyeOff, FileText, ListChecks, Pause, Play, RefreshCw, Settings2, Square, Terminal } from "lucide-react";
import { Navigate, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";
import type { Feature, Task } from "../types";

const tabs = [
  ["config", "配置", Settings2], ["features", "功能清单", ListChecks], ["run", "运行", Terminal],
  ["screenshots", "截图审核", Eye], ["report", "报告", FileText],
] as const;

export function TaskWorkspace() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const task = useQuery({ queryKey: ["task", taskId], queryFn: () => api.getTask(taskId), refetchInterval: 2500 });
  useEffect(() => {
    const source = new EventSource(`/api/tasks/${taskId}/events`);
    const refresh = () => task.refetch();
    ["status", "step", "approval"].forEach((event) => source.addEventListener(event, refresh));
    return () => source.close();
  }, [taskId]);
  if (task.isLoading) return <div className="workspace-loading"><span /><span /><span /></div>;
  if (!task.data) return <div className="page"><p className="form-error">{task.error?.message || "任务不存在"}</p></div>;
  return <div className="workspace">
    <div className="workspace-header">
      <button className="icon-button" title="返回任务列表" onClick={() => navigate("/")}><ChevronLeft size={19} /></button>
      <div className="workspace-title"><h1>{task.data.name}</h1><span>{task.data.start_url || "尚未确认访问地址"}</span></div>
      <StatusBadge status={task.data.status} />
    </div>
    {task.data.error && <div className={task.data.status === "awaiting_review" ? "notice-banner" : "error-banner"}><CircleAlert size={18} /><span>{task.data.error}</span></div>}
    <nav className="tabs">{tabs.map(([path, label, Icon]) => <NavLink key={path} to={`/tasks/${taskId}/${path}`} className={({ isActive }) => isActive ? "active" : ""}><Icon size={16} />{label}</NavLink>)}</nav>
    <Routes>
      <Route path="config" element={<ConfigView task={task.data} refresh={task.refetch} />} />
      <Route path="features" element={<FeaturesView task={task.data} refresh={task.refetch} />} />
      <Route path="run" element={<RunView task={task.data} refresh={task.refetch} />} />
      <Route path="screenshots" element={<ScreenshotsView task={task.data} refresh={task.refetch} />} />
      <Route path="report" element={<ReportView task={task.data} refresh={task.refetch} />} />
      <Route path="*" element={<Navigate to={`/tasks/${taskId}/config`} replace />} />
    </Routes>
  </div>;
}

function ConfigView({ task, refresh }: ViewProps) {
  const navigate = useNavigate();
  const [plan, setPlan] = useState(task.launch_plan);
  const [startUrl, setStartUrl] = useState(task.start_url || "");
  const [browserMode, setBrowserMode] = useState(task.browser_mode);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  useEffect(() => { setPlan(task.launch_plan); setStartUrl(task.start_url || ""); }, [task.launch_plan, task.start_url]);
  const analyze = useMutation({ mutationFn: () => api.analyze(task.id), onSuccess: () => refresh() });
  const save = useMutation({
    mutationFn: async (event: FormEvent) => {
      event.preventDefault();
      if (!plan) throw new Error("请先分析源码");
      await api.credentials(task.id, { username, password });
      return api.review(task.id, { start_url: startUrl, browser_mode: browserMode, launch_plan: plan, features: task.features });
    },
    onSuccess: async () => {
      await refresh();
      navigate(`/tasks/${task.id}/run`);
    },
  });
  return <div className="view-content config-view">
    {!plan ? <section className="action-panel"><div><h2>分析源码</h2><p>识别项目类型、启动方式、页面路由和功能入口。分析不会安装依赖或执行源码。</p></div><button className="button primary" onClick={() => analyze.mutate()} disabled={analyze.isPending}><RefreshCw size={16} />{analyze.isPending ? "分析中" : "开始分析"}</button></section> :
    <form onSubmit={(event) => save.mutate(event)}>
      <section className="form-section"><div className="section-heading"><h2>启动方案</h2><span>{plan.project_type}</span></div>
        <div className="form-grid">
          <label>访问地址<input required value={startUrl} onChange={(e) => setStartUrl(e.target.value)} /></label>
          <label>工作目录<input required value={plan.working_directory} onChange={(e) => setPlan({ ...plan, working_directory: e.target.value })} /></label>
          <label className="full">安装命令<input value={plan.install_command.join(" ")} onChange={(e) => setPlan({ ...plan, install_command: splitCommand(e.target.value) })} /></label>
          <label className="full">启动命令<input required value={plan.start_command.join(" ")} onChange={(e) => setPlan({ ...plan, start_command: splitCommand(e.target.value) })} /></label>
        </div>
      </section>
      <section className="form-section"><div className="section-heading"><h2>浏览器与登录</h2><span>凭据仅保存在当前进程内存</span></div>
        <div className="form-grid">
          <label>浏览器模式<select value={browserMode} onChange={(e) => setBrowserMode(e.target.value as "headed" | "headless")}><option value="headed">有界面</option><option value="headless">无头</option></select></label>
          <span />
          <label>测试账号<input autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          <label>测试密码<input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        </div>
      </section>
      <div className="sticky-actions"><button type="button" className="button secondary" onClick={() => analyze.mutate()} disabled={save.isPending || analyze.isPending}><RefreshCw size={16} />{analyze.isPending ? "分析中" : "重新分析"}</button><button className="button primary" disabled={save.isPending || analyze.isPending}><Check size={16} />{save.isPending ? "确认中" : "确认方案"}</button></div>
      {(save.error || analyze.error) && <p className="form-error">{save.error?.message || analyze.error?.message}</p>}
    </form>}
  </div>;
}

function FeaturesView({ task, refresh }: ViewProps) {
  const [features, setFeatures] = useState(task.features);
  useEffect(() => setFeatures(task.features), [task.features]);
  const save = useMutation({ mutationFn: () => task.launch_plan ? api.review(task.id, { start_url: task.start_url, browser_mode: task.browser_mode, launch_plan: task.launch_plan, features }) : Promise.reject(new Error("请先分析源码")), onSuccess: () => refresh() });
  const update = (index: number, patch: Partial<Feature>) => setFeatures((items) => items.map((item, i) => i === index ? { ...item, ...patch } : item));
  return <div className="view-content"><div className="view-heading"><div><h2>功能清单</h2><p>确认 AI 需要覆盖的功能和每项探索目标。</p></div><button className="button primary" onClick={() => save.mutate()} disabled={!task.launch_plan}><Check size={16} />保存清单</button></div>
    <div className="feature-editor"><div className="feature-head"><span>覆盖</span><span>功能名称</span><span>入口</span><span>探索目标</span></div>
      {features.map((feature, index) => <div className="feature-edit-row" key={feature.id}><input aria-label={`覆盖${feature.title}`} type="checkbox" checked={feature.selected} onChange={(e) => update(index, { selected: e.target.checked })} /><input aria-label="功能名称" value={feature.title} onChange={(e) => update(index, { title: e.target.value })} /><input aria-label="入口" value={feature.entry_path} onChange={(e) => update(index, { entry_path: e.target.value })} /><input aria-label="探索目标" value={feature.goal} onChange={(e) => update(index, { goal: e.target.value })} /></div>)}
    </div>{save.error && <p className="form-error">{save.error.message}</p>}</div>;
}

function RunView({ task, refresh }: ViewProps) {
  const client = useQueryClient();
  const action = useMutation({ mutationFn: (name: string) => api.action(task.id, name), onSuccess: () => { refresh(); client.invalidateQueries({ queryKey: ["tasks"] }); } });
  const steps = task.features.flatMap((feature) => feature.steps.map((step) => ({ ...step, feature: feature.title })));
  const latest = [...steps].reverse().find((step) => step.screenshot);
  const active = ["queued", "installing", "starting", "authenticating", "exploring", "generating"].includes(task.status);
  return <div className="run-view">
    <div className="run-toolbar"><div><StatusBadge status={task.status} /><span>{task.features.filter((item) => item.status === "completed").length} / {task.features.filter((item) => item.selected).length} 项完成</span></div><div>
      {!active && <button className="button primary" onClick={() => action.mutate("run")}><Play size={16} />开始运行</button>}
      {active && <button className="button secondary" onClick={() => action.mutate("pause")}><Pause size={16} />暂停</button>}
      {(active || task.status === "paused") && <button className="button danger" onClick={() => action.mutate("cancel")}><Square size={15} />终止</button>}
    </div></div>
    <div className="run-grid">
      <aside className="feature-progress"><h3>功能进度</h3>{task.features.filter((item) => item.selected).map((feature) => <div className="progress-row" key={feature.id}><span className={`progress-dot ${feature.status}`} /> <div><strong>{feature.title}</strong><span>{featureStatusLabel(feature.status)}</span>{feature.error && <p className="progress-error">{conciseError(feature.error)}</p>}</div></div>)}</aside>
      <section className="browser-preview">{latest?.screenshot ? <><img src={latest.screenshot.url} alt={latest.instruction} /><div className="preview-caption"><strong>{latest.feature}</strong><span>{latest.instruction}</span></div></> : <div className="preview-empty"><Eye size={28} /><strong>{active ? "任务正在运行" : "暂无页面截图"}</strong><span>{runEmptyMessage(task.status)}</span></div>}</section>
      <aside className="event-panel"><h3>事件记录</h3>
        <div className="event-list">{[...steps].reverse().slice(0, 20).map((step) => <div key={step.id}><span>{step.action}</span><p>{step.instruction}</p></div>)}{steps.length === 0 && <p className="muted">还没有操作记录。</p>}</div>
      </aside>
    </div>
  </div>;
}

function ScreenshotsView({ task, refresh }: ViewProps) {
  const screenshots = task.features.flatMap((feature) => feature.steps.filter((step) => step.screenshot).map((step) => ({ feature: feature.title, step, screenshot: step.screenshot! })));
  const update = useMutation({ mutationFn: ({ id, included }: { id: string; included: boolean }) => api.screenshot(task.id, id, included), onSuccess: () => refresh() });
  return <div className="view-content"><div className="view-heading"><div><h2>截图审核</h2><p>仅勾选的原始截图会进入最终手册。</p></div><span className="count">{screenshots.filter((item) => item.screenshot.included).length} / {screenshots.length}</span></div>
    {screenshots.length === 0 ? <div className="empty"><EyeOff size={24} /><strong>暂无截图</strong><span>完成探索后可在这里审核。</span></div> : <div className="screenshot-grid">{screenshots.map(({ feature, step, screenshot }) => <article key={screenshot.id} className={!screenshot.included ? "excluded" : ""}><img src={screenshot.url} alt={step.instruction} /><div><label><input type="checkbox" checked={screenshot.included} onChange={(e) => update.mutate({ id: screenshot.id, included: e.target.checked })} />纳入手册</label><strong>{feature}</strong><p>{step.instruction}</p></div></article>)}</div>}
  </div>;
}

function ReportView({ task, refresh }: ViewProps) {
  const report = useMutation({ mutationFn: () => api.report(task.id), onSuccess: () => refresh() });
  const selected = task.features.filter((item) => item.selected);
  const completed = selected.filter((item) => item.status === "completed" && item.steps.length > 0);
  const incompleteCount = selected.length - completed.length;
  const reportReady = completed.length > 0;
  return <div className="view-content"><div className="report-layout"><div className="report-summary"><FileText size={26} /><h2>生成用户操作手册</h2><p>按已确认的功能步骤和截图生成中文 DOCX，并在后台完成逐页版面检查。</p><dl><div><dt>已完成功能</dt><dd>{completed.length}</dd></div><div><dt>纳入截图</dt><dd>{task.features.flatMap((item) => item.steps).filter((step) => step.screenshot?.included).length}</dd></div></dl><button className="button primary" disabled={report.isPending || !reportReady} onClick={() => report.mutate()}><FileText size={16} />{report.isPending ? "生成中" : incompleteCount > 0 ? "生成部分报告" : "生成报告"}</button>{!reportReady && <p className="report-requirement">至少完成一项功能探索并记录操作步骤后才能生成报告。</p>}{reportReady && incompleteCount > 0 && <p className="report-requirement">仍有 {incompleteCount} 项功能未完成，报告会标记失败项，任务继续保持待审核。</p>}{report.error && <p className="form-error">{report.error.message}</p>}</div>
      <div className="artifact-list"><h3>可下载文件</h3>{task.artifacts.length === 0 ? <p className="muted">报告生成后显示在这里。</p> : task.artifacts.map((artifact) => <a href={artifact.url} key={artifact.id}><span className={`file-kind ${artifact.kind}`}>{artifact.kind.toUpperCase()}</span><div><strong>{artifact.name}</strong><span>{formatSize(artifact.size)}</span></div><Download size={18} /></a>)}</div></div></div>;
}

interface ViewProps { task: Task; refresh: () => unknown; }
const featureStatusLabels: Record<string, string> = {
  pending: "等待运行", processing: "正在探索", completed: "已完成", failed: "失败", skipped: "已跳过",
};
function featureStatusLabel(status: string) { return featureStatusLabels[status] || status; }
function conciseError(error: string) { return error.split("\n").find((line) => line.trim()) || error; }
function runEmptyMessage(status: Task["status"]) {
  const messages: Partial<Record<Task["status"], string>> = {
    queued: "任务已进入队列。", installing: "正在安装项目依赖。", starting: "正在启动待测应用。",
    authenticating: "应用已启动，正在准备浏览器。", exploring: "正在等待模型选择并执行下一步操作。",
  };
  return messages[status] || "运行成功并记录操作后将在这里显示截图。";
}
function splitCommand(value: string) { return value.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((item) => item.replace(/^"|"$/g, "")) || []; }
function formatSize(bytes: number) { return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`; }
