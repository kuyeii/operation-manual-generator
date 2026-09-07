import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Check, Download, Eye, FileText, Pencil, RefreshCw, Save, X } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Business, Chapter, CopyrightState, Operation, Stage } from "../copyright";
import "./copyright.css";

const stages: [Stage, string][] = [["business", "业务理解"], ["registration", "登记信息"], ["sources", "源码选择"], ["drafts", "草稿审核"]];
const options: Record<string, string[]> = {
  software_category: ["应用软件", "嵌入式软件", "中间件", "系统软件", "其他"],
  owner_type: ["自然人", "法人", "其他组织"], development_method: ["单独开发", "合作开发", "委托开发", "下达任务开发"],
  originality: ["原创", "修改"], publication_status: ["已发表", "未发表"], rights_acquisition: ["原始取得", "继受取得"],
};
const longFields = new Set(["ownership_notes", "main_functions", "technical_features", "purpose"]);

export function CopyrightView({ taskId, downloadsOnly = false }: { taskId: string; downloadsOnly?: boolean }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["copyright", taskId], queryFn: () => api.copyright(taskId), refetchInterval: 2500 });
  const [stage, setStage] = useState<Stage>("business");
  const [editing, setEditing] = useState(false);
  const [editRevision, setEditRevision] = useState(0);
  const [values, setValues] = useState<CopyrightState["data"]>({});
  useEffect(() => {
    const events = new EventSource(`/api/tasks/${taskId}/events`);
    const refresh = () => { void client.invalidateQueries({ queryKey: ["copyright", taskId] }); void client.invalidateQueries({ queryKey: ["task", taskId] }); };
    events.addEventListener("copyright", refresh);
    return () => events.close();
  }, [taskId, client]);
  useEffect(() => { setEditing(false); setStage("business"); }, [taskId]);
  const refresh = async () => {
    await query.refetch();
    await client.invalidateQueries({ queryKey: ["task", taskId] });
  };
  const generation = useMutation({ mutationFn: (operation: Operation) => api.copyrightGenerate(taskId, operation, query.data!.revision), onSuccess: refresh });
  const save = useMutation({ mutationFn: () => api.copyrightSave(taskId, stage, editRevision, values[stage]), onSuccess: async () => { setEditing(false); await refresh(); } });
  const confirm = useMutation({ mutationFn: () => api.copyrightConfirm(taskId, stage, query.data!.revision), onSuccess: refresh });
  if (!query.data) return <div className="view-content">{query.error ? <p className="form-error">{query.error.message}</p> : <p className="muted">正在加载软著资料…</p>}</div>;
  const state = query.data;
  const completed = state.blockers.length === 0 && state.batches.some((batch) => batch.status === "published" && batch.revision === state.revision);
  const busy = state.status === "running" || generation.isPending || save.isPending || confirm.isPending;
  const error = save.error || confirm.error || generation.error || query.error;
  const startEdit = () => { setValues(structuredClone(state.data)); setEditRevision(state.revision); setEditing(true); save.reset(); confirm.reset(); };
  const current = editing ? values : state.data;
  const selectedIndex = stages.findIndex(([key]) => key === stage);
  const allowed = selectedIndex === 0 || !!state.confirmations[stages[selectedIndex - 1][0]];
  const changeStage = (next: Stage) => { setStage(next); save.reset(); confirm.reset(); };
  return <div className="view-content copyright-view">
    <div className="view-heading"><div><h2>{downloadsOnly ? "软著材料" : "软件著作权资料"}</h2><span className="muted">{state.data.registration?.software_name || "尚未分析"} {state.data.registration?.version || ""}</span></div>
      {!downloadsOnly && <button className="button secondary" disabled={busy || editing} onClick={() => generation.mutate("analyze")}><RefreshCw size={16} />{state.data.business ? "重新分析" : "分析软著资料"}</button>}
    </div>
    {state.progress && <div className="copyright-progress" role="status"><span className={state.status === "running" ? "status status-analyzing" : completed ? "status status-completed" : "status"}>{state.status === "running" ? "处理中" : state.status === "failed" ? "失败" : completed ? "已完成" : "待审核"}</span>{state.progress}</div>}
    {(state.error || error) && <p className="form-error" role="alert">{error?.message || state.error}</p>}
    {state.status === "failed" && state.operation && <button className="button secondary" disabled={busy || editing} onClick={() => generation.mutate(state.operation!)}><RefreshCw size={16} />重试当前阶段</button>}
    {!downloadsOnly && <>
      <nav className="copyright-stages" aria-label="软著审核阶段">{stages.map(([key, label], index) => <button key={key} aria-current={stage === key ? "step" : undefined} disabled={editing} onClick={() => changeStage(key)}><span>{state.confirmations[key] ? <Check size={15} /> : index + 1}</span>{label}</button>)}</nav>
      {editing && state.revision !== editRevision && <p className="form-error">资料已有新版本，本地编辑内容已保留；请取消编辑并刷新后重新修改。</p>}
      <div className="copyright-stage-heading"><h3>{stages[selectedIndex][1]}</h3>{current[stage] && allowed && !editing && <button className="button secondary" disabled={busy} onClick={startEdit}><Pencil size={15} />修改</button>}</div>
      {!allowed && <p className="notice-banner">请先确认{stages[selectedIndex - 1][1]}。</p>}
      {stage === "business" && current.business && <BusinessEditor business={current.business} editing={editing} onChange={(business) => setValues({ ...values, business })} />}
      {stage === "registration" && current.registration && <div className="form-grid copyright-fields">{Object.entries(state.field_labels).map(([key, label]) => <label key={key} className={longFields.has(key) ? "full" : ""}>{label}{editing ? options[key] ? <select value={current.registration![key]} onChange={(e) => setValues({ ...values, registration: { ...current.registration!, [key]: e.target.value, ...(key === "publication_status" && e.target.value === "未发表" ? { publication_date: "", publication_place: "" } : {}) } })}><option value="">请选择</option>{options[key].map((value) => <option key={value}>{value}</option>)}</select> : longFields.has(key) ? <textarea value={current.registration![key]} onChange={(e) => setValues({ ...values, registration: { ...current.registration!, [key]: e.target.value } })} /> : <input type={key.endsWith("_date") ? "date" : "text"} value={current.registration![key]} onChange={(e) => setValues({ ...values, registration: { ...current.registration!, [key]: e.target.value } })} /> : <span className="copyright-value">{current.registration![key] || "未填写"}</span>}</label>)}</div>}
      {stage === "sources" && current.sources && state.data.inventory && <SourceEditor state={state} paths={current.sources.paths} editing={editing} onChange={(paths) => setValues({ ...values, sources: { paths } })} />}
      {stage === "sources" && current.sources && !editing && <SourcePreview taskId={taskId} revision={state.revision} />}
      {stage === "drafts" && <>
        <div className="copyright-actions"><button className="button secondary" disabled={busy || editing || !allowed} onClick={() => generation.mutate("draft")}><FileText size={16} />{current.drafts ? "重新生成草稿" : "生成全部草稿"}</button>
          {current.drafts && <button className="button secondary" disabled={busy || editing || !allowed} onClick={() => generation.mutate("review")}><RefreshCw size={16} />复核草稿</button>}
          {state.data.registration && <a className="button secondary" href={`/api/tasks/${taskId}/copyright/draft-download`}><Download size={16} />下载草稿</a>}
          <Link className="button secondary" to={`/tasks/${taskId}/screenshots`}><Eye size={16} />截图审核</Link>
        </div>
        {state.blockers.length > 0 && <ul className="copyright-blockers">{state.blockers.map((message, index) => <li key={index}>{message}</li>)}</ul>}
        {(state.data.review_issues || []).map((issue) => <ReviewIssue key={issue} taskId={taskId} state={state} issue={issue} disabled={busy || editing} refresh={refresh} />)}
        {current.drafts && <DraftEditor taskId={taskId} drafts={current.drafts} editing={editing} onChange={(drafts) => setValues({ ...values, drafts })} />}
      </>}
      {!current[stage] && stage !== "drafts" && <p className="muted">尚未生成{stages[selectedIndex][1]}。</p>}
      {current[stage] && <div className="copyright-actions copyright-stage-actions">{editing ? <><button className="button secondary" disabled={save.isPending} onClick={() => setEditing(false)}><X size={16} />取消</button><button className="button primary" disabled={busy || state.revision !== editRevision} onClick={() => save.mutate()}><Save size={16} />保存修改</button></> : <button className="button primary" disabled={busy || !allowed || !!state.confirmations[stage] || (stage === "drafts" && state.blockers.length > 0)} onClick={() => confirm.mutate()}><Check size={16} />{state.confirmations[stage] ? "已确认" : `确认${stages[selectedIndex][1]}`}</button>}</div>}
    </>}
    <section className="copyright-delivery"><div className="copyright-stage-heading"><h3>正式材料</h3><button className="button primary" disabled={busy || editing || !state.confirmations.drafts || state.blockers.length > 0} onClick={() => generation.mutate("publish")}><FileText size={16} />生成正式材料</button></div>
      {downloadsOnly && !state.confirmations.drafts && <Link className="button secondary" to={`/tasks/${taskId}/copyright`}>前往软著资料审核</Link>}
      {state.batches.length === 0 && <p className="muted">尚无正式材料。</p>}
      {state.batches.map((batch) => <div className="copyright-batch" key={batch.id}><h4>版本 {batch.revision} · {new Date(batch.created_at).toLocaleString()} · {batch.status === "published" ? "已发布" : batch.status === "failed" ? "未发布" : "生成中"}</h4>{batch.error && <p className="form-error">{batch.error}</p>}<div className="artifact-list">{batch.files.map((file) => <a key={file.url} href={file.url}><span className="file-kind">{file.kind.toUpperCase()}</span><div><strong>{file.name}</strong><span>{file.formal ? "正式材料" : "内部核验记录"} · {Math.ceil(file.size / 1024)} KB</span></div><Download size={16} /></a>)}</div></div>)}
    </section>
    <details className="copyright-rules"><summary>规则来源与交存范围</summary><p>{state.rules.baseline}</p><p>{state.rules.notice}</p></details>
  </div>;
}

function ReviewIssue({ taskId, state, issue, disabled, refresh }: { taskId: string; state: CopyrightState; issue: string; disabled: boolean; refresh: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const resolved = Object.values(state.data.review_resolutions || {}).find((item) => item.issue === issue);
  const save = useMutation({ mutationFn: () => api.copyrightResolve(taskId, state.revision, issue, note), onSuccess: refresh });
  return <section className="copyright-feature">
    <p>{issue}</p>
    {resolved && <><small>已人工核对</small><p>{resolved.note}</p></>}
    {!resolved && !open && <div><button className="button secondary" disabled={disabled} onClick={() => setOpen(true)}><Pencil size={15} />记录人工核对</button></div>}
    {!resolved && open && <>
      <label>源码核对依据与处理理由<textarea value={note} onChange={(e) => setNote(e.target.value)} minLength={20} /></label>
      <div className="copyright-actions">
        <button className="button secondary" disabled={save.isPending} onClick={() => setOpen(false)}><X size={15} />取消</button>
        <button className="button secondary" disabled={disabled || save.isPending || note.trim().length < 20} onClick={() => save.mutate()}><Check size={15} />确认已核对</button>
      </div>
      {save.error && <p className="form-error">{save.error.message}</p>}
    </>}
  </section>;
}

function BusinessEditor({ business, editing, onChange }: { business: Business; editing: boolean; onChange: (value: Business) => void }) {
  const labels = { positioning: "产品定位", industry: "面向领域", users: "目标用户", purpose: "开发目的", development_environment: "开发环境证据", runtime_environment: "运行环境证据" };
  return <div className="copyright-business"><div className="form-grid copyright-fields">{Object.entries(labels).map(([key, label]) => <label key={key}>{label}{editing ? <textarea value={business[key as keyof typeof labels]} onChange={(e) => onChange({ ...business, [key]: e.target.value })} /> : <span className="copyright-value">{business[key as keyof typeof labels] || "未识别"}</span>}</label>)}</div><h4>业务功能与源码证据</h4>{business.features.map((feature, index) => <section className="copyright-feature" key={index}>{editing ? <><input aria-label="功能名称" value={feature.title} onChange={(e) => onChange({ ...business, features: business.features.map((item, i) => i === index ? { ...item, title: e.target.value } : item) })} /><textarea aria-label="功能描述" value={feature.description} onChange={(e) => onChange({ ...business, features: business.features.map((item, i) => i === index ? { ...item, description: e.target.value } : item) })} /></> : <><h4>{feature.title}</h4><p>{feature.description}</p></>}<small>{feature.evidence_ids.join(" · ")}</small></section>)}</div>;
}

function SourceEditor({ state, paths, editing, onChange }: { state: CopyrightState; paths: string[]; editing: boolean; onChange: (paths: string[]) => void }) {
  const files = state.data.inventory!.files;
  const sorted = [...paths, ...files.map((file) => file.path).filter((path) => !paths.includes(path))];
  const move = (index: number, delta: number) => { const result = [...paths]; [result[index], result[index + delta]] = [result[index + delta], result[index]]; onChange(result); };
  return <><p className="muted">已选 {paths.length} 个文件 · 项目源码 {state.data.inventory!.source_lines} 行</p><div className="copyright-sources">{sorted.map((path) => { const file = files.find((item) => item.path === path)!; const index = paths.indexOf(path); return <div key={path} className="copyright-source-row">{editing && <input aria-label={`选择 ${path}`} type="checkbox" checked={index >= 0} onChange={(e) => onChange(e.target.checked ? [...paths, path] : paths.filter((item) => item !== path))} />}<details><summary>{index >= 0 ? `${index + 1}. ` : "未选 · "}{path} <small>{file.lines} 行</small></summary><pre>{file.excerpt}</pre></details>{editing && index >= 0 && <div className="copyright-actions"><button title="上移" className="icon-button" disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={15} /></button><button title="下移" className="icon-button" disabled={index === paths.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} /></button></div>}</div>; })}</div>{state.data.inventory!.excluded.length > 0 && <details className="copyright-rules"><summary>已排除文件（{state.data.inventory!.excluded.length}）</summary>{state.data.inventory!.excluded.map((item) => <p key={item.path}>{item.path}：{item.reason}</p>)}</details>}</>;
}

function SourcePreview({ taskId, revision }: { taskId: string; revision: number }) {
  const [open, setOpen] = useState(false);
  const preview = useQuery({ queryKey: ["copyright-source", taskId, revision], queryFn: () => api.copyrightSource(taskId), enabled: open });
  return <details className="copyright-rules" onToggle={(e) => setOpen(e.currentTarget.open)}><summary>源程序分页预览</summary>{preview.error && <p className="form-error">{preview.error.message}</p>}{preview.isFetching && <p>正在分页…</p>}{preview.data && <><p>完整源码 {preview.data.total_pages} 页 · {preview.data.selected_lines} 行有效原始源码</p>{preview.data.volumes.map((volume) => <details key={volume.name}><summary>{volume.name} · {volume.pages.length} 页</summary>{volume.pages.map((page) => <details key={page.number}><summary>第 {page.number} 页</summary><pre>{page.rows.map((row) => row.text).join("\n")}</pre></details>)}</details>)}</>}</details>;
}

function DraftEditor({ taskId, drafts, editing, onChange }: { taskId: string; drafts: { manual: Chapter[]; design: Chapter[] }; editing: boolean; onChange: (drafts: { manual: Chapter[]; design: Chapter[] }) => void }) {
  return <div className="copyright-chapters">{([ ["manual", "操作手册"], ["design", "技术设计说明书"] ] as const).map(([key, title]) => <section key={key}><h3>{title}</h3>{drafts[key].map((section, index) => <details key={index} open={editing || undefined}><summary>{section.title}</summary>{editing ? <textarea aria-label={section.title} rows={12} value={section.paragraphs.join("\n\n")} onChange={(e) => onChange({ ...drafts, [key]: drafts[key].map((item, i) => i === index ? { ...item, paragraphs: e.target.value.split(/\n\s*\n/) } : item) })} /> : section.paragraphs.map((paragraph, i) => <p key={i}>{paragraph}</p>)}{section.screenshot_ids.map((id) => <img key={id} loading="lazy" src={`/api/tasks/${taskId}/screenshots/${id}/file`} alt={`${section.title}截图`} />)}<small>源码证据：{section.evidence_ids.join(" · ")}</small></details>)}</section>)}</div>;
}
