import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, FileArchive, Plus, Trash2, Upload } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { StatusBadge } from "../components/StatusBadge";

export function TaskList() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File>();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: api.listTasks, refetchInterval: 3000 });
  const create = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("请选择 ZIP 源码包");
      const data = new FormData();
      data.append("name", name || file.name.replace(/\.zip$/i, ""));
      data.append("source", file);
      return api.createTask(data);
    },
    onSuccess: (task) => navigate(`/tasks/${task.id}/config`),
  });
  const remove = useMutation({ mutationFn: api.remove, onSuccess: () => client.invalidateQueries({ queryKey: ["tasks"] }) });

  return <div className="page task-list-page">
    <div className="page-heading"><div><h1>任务</h1><p>软件著作权材料与操作手册</p></div></div>
    <section className="upload-band" aria-label="新建任务">
      <div className="upload-copy"><div className="section-icon"><Upload size={20} /></div><div><h2>新建生成任务</h2><p>源码包将保存在本机任务目录。</p></div></div>
      <div className="upload-controls">
        <input aria-label="任务名称" placeholder="任务名称" value={name} onChange={(event) => setName(event.target.value)} />
        <input ref={input} hidden type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0])} />
        <button className="button secondary" onClick={() => input.current?.click()}><FileArchive size={16} />{file?.name || "选择 ZIP"}</button>
        <button className="button primary" disabled={!file || create.isPending} onClick={() => create.mutate()}><Plus size={16} />{create.isPending ? "上传中" : "创建任务"}</button>
      </div>
      {create.error && <p className="form-error">{create.error.message}</p>}
    </section>
    <section className="task-table" aria-label="任务列表">
      <div className="table-head"><span>项目</span><span>状态</span><span>更新时间</span><span aria-label="操作" /></div>
      {tasks.isLoading && <div className="table-loading"><span /><span /><span /></div>}
      {tasks.data?.length === 0 && <div className="empty"><FileArchive size={24} /><strong>还没有任务</strong><span>上传第一个源码 ZIP 开始。</span></div>}
      {tasks.data?.map((task) => <div className="task-row" key={task.id}>
        <button className="task-name" onClick={() => navigate(`/tasks/${task.id}/config`)}>{task.name}</button>
        <StatusBadge status={task.status} />
        <time>{new Date(task.updated_at).toLocaleString("zh-CN")}</time>
        <div className="row-actions">
          <button className="icon-button danger-quiet" title="删除任务" onClick={() => confirm(`删除“${task.name}”及其全部产物？`) && remove.mutate(task.id)}><Trash2 size={16} /></button>
          <button className="icon-button" title="打开任务" onClick={() => navigate(`/tasks/${task.id}/config`)}><ArrowRight size={17} /></button>
        </div>
      </div>)}
    </section>
  </div>;
}
