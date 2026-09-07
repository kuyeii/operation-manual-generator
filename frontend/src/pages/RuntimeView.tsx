import { useState, useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Download, Pencil, Plus, RefreshCw, Save, Trash2, Upload, X } from "lucide-react";
import { runtimeApi, emptyFeature } from "../runtime";
import type { RuntimeState, Service } from "../runtime";
import type { Task } from "../types";
import { api, request } from "../api";
import "./runtime.css";

export function RuntimeView({ task, mode="plan" }: {task:Task;mode?:"plan"|"execution"}) {
  const query=useQuery({queryKey:["runtime",task.id],queryFn:()=>runtimeApi.state(task.id),refetchInterval:3000});
  const [draft,setDraft]=useState<RuntimeState|null>(null);
  const [file,setFile]=useState<File|null>(null);
  const [purpose,setPurpose]=useState("");
  const [credentials,setCredentials]=useState({username:"",password:""});
  const [localError,setLocalError]=useState("");
  const mutation=useMutation({mutationFn:({path,method,value}:{path:string;method:string;value:unknown})=>runtimeApi.mutate(task.id,path,method,value),onSuccess:async()=>{setDraft(null);await query.refetch();}});
  const upload=useMutation({mutationFn:async()=>{const data=new FormData();data.append("file",file!);data.append("purpose",purpose);data.append("revision",String(query.data!.revision));await runtimeApi.upload(task.id,data);},onSuccess:async()=>{setFile(null);setPurpose("");await query.refetch();}});
  const remove=useMutation({mutationFn:(id:string)=>runtimeApi.remove(task.id,id,query.data!.revision),onSuccess:()=>query.refetch()});
  const login=useMutation({mutationFn:()=>api.credentials(task.id,credentials)});
  const analyze=useMutation({mutationFn:()=>api.analyze(task.id),onSuccess:()=>query.refetch()});
  const data=draft||query.data;
  if(!data) return <div className="view-content">{query.error?.message||"正在读取运行配置…"}</div>;
  const active=["queued","analyzing","installing","starting","authenticating","exploring","generating"].includes(task.status);
  const busy=active||mutation.isPending||upload.isPending||remove.isPending;
  const error=localError||mutation.error?.message||upload.error?.message||remove.error?.message||login.error?.message||analyze.error?.message;
  const mutate=(path:string,value:unknown,method="POST")=>{
    const invalid = document.querySelector<HTMLTextAreaElement>(".runtime-view textarea:invalid");
    if (invalid) { invalid.reportValidity(); return; }
    mutation.mutate({path,value,method});
  };
  const updateService=(index:number,patch:Partial<Service>)=>setDraft({...data,plan:{...data.plan,services:data.plan.services.map((s,i)=>i===index?{...s,...patch}:s)}});
  const path=mode==="plan"?"runtime-plan":"execution-config";
  return <div className="view-content runtime-view">
    <div className="view-heading"><h2>{mode==="plan"?"运行方案":"功能依赖与测试文件用途"}</h2>{!draft&&<button className="button secondary" disabled={busy} onClick={()=>{setDraft(structuredClone(data));setLocalError("");}}><Pencil size={16}/>修改</button>}</div>
    {error&&<p className="form-error" role="alert">{error}</p>}
    {draft&&draft.revision!==query.data?.revision&&<p className="form-error">配置已更新，当前输入已保留；请取消并重新修改。</p>}
    {data.blockers.length>0&&<ul className="runtime-blockers">{data.blockers.map((b,i)=><li key={i}>{b}</li>)}</ul>}
    {mode==="plan" ? <>
      <div className="runtime-actions"><button className="button secondary" disabled={busy||!!draft} onClick={()=>mutate("runtime-plan/detect",{revision:data.revision})}><RefreshCw size={16}/>重新识别</button><button className="button secondary" disabled={busy||!!draft||analyze.isPending} onClick={()=>analyze.mutate()}><RefreshCw size={16}/>分析功能</button></div>
      {data.plan.services.map((s,index)=><section className="runtime-service" key={index}>
        <div className="section-heading"><h3>{s.id} · {s.framework}</h3>{draft&&<button className="icon-button" title="移除服务" onClick={()=>setDraft({...data,plan:{...data.plan,services:data.plan.services.filter((_,i)=>i!==index)}})}><Trash2 size={16}/></button>}</div>
        {!draft?<dl className="runtime-facts"><dt>工作目录</dt><dd>{s.directory}</dd><dt>运行环境</dt><dd>{s.runtime} {s.image}</dd><dt>启动命令</dt><dd><code>{s.command.join(" ")||s.dockerfile}</code></dd><dt>端口 / 健康检查</dt><dd>{s.port} {s.health_path}</dd><dt>证据</dt><dd>{s.evidence.join("、")}</dd></dl>:<div className="form-grid">
          <label>服务标识<input value={s.id} onChange={e=>updateService(index,{id:e.target.value})}/></label>
          <label>运行时<select value={s.runtime} onChange={e=>updateService(index,{runtime:e.target.value})}>{["node","python","go","static","docker","compose","custom"].map(r=><option key={r}>{r}</option>)}</select></label>
          <label>工作目录<input value={s.directory} onChange={e=>updateService(index,{directory:e.target.value})}/></label><label>基础镜像<input value={s.image} onChange={e=>updateService(index,{image:e.target.value})}/></label>
          <label>业务端口<input type="number" min={1} max={65535} value={s.port} onChange={e=>updateService(index,{port:Number(e.target.value)})}/></label><label>健康检查路径<input value={s.health_path} onChange={e=>updateService(index,{health_path:e.target.value})}/></label>
          <label>Dockerfile / Compose 文件<input value={s.dockerfile} onChange={e=>updateService(index,{dockerfile:e.target.value})}/></label><label>嵌入前端目录<input value={s.embed_frontend||""} onChange={e=>updateService(index,{embed_frontend:e.target.value||null})}/></label>
          <JsonEditor label="启动参数" value={s.command} onChange={v=>updateService(index,{command:v as string[]})} onError={setLocalError}/><JsonEditor label="安装步骤" value={s.install} onChange={v=>updateService(index,{install:v as string[][]})} onError={setLocalError}/><JsonEditor label="构建步骤" value={s.build} onChange={v=>updateService(index,{build:v as string[][]})} onError={setLocalError}/><JsonEditor label="环境变量" value={s.environment} onChange={v=>updateService(index,{environment:v as Record<string,string>})} onError={setLocalError}/>
          <fieldset className="full"><legend>前置服务</legend>{data.plan.services.filter(other=>other.id!==s.id).map(other=><label className="runtime-check" key={other.id}><input type="checkbox" checked={s.depends_on.includes(other.id)} onChange={e=>updateService(index,{depends_on:e.target.checked?[...s.depends_on,other.id]:s.depends_on.filter(id=>id!==other.id)})}/>{other.id}</label>)}</fieldset>
        </div>}
      </section>)}
      {draft&&<button className="button secondary" onClick={()=>setDraft({...data,plan:{...data.plan,services:[...data.plan.services,{id:`service-${data.plan.services.length+1}`,runtime:"node",framework:"custom",directory:".",image:"node:22-bookworm-slim",port:8080,health_path:"/",dockerfile:"Dockerfile",embed_frontend:null,install:[],build:[],command:[],environment:{},depends_on:[],evidence:[]}]}})}><Plus size={16}/>添加服务</button>}
      <section className="form-section"><h3>业务页面就绪条件</h3><div className="form-grid">{draft?<>
        <label>入口服务<select value={data.plan.entry_service} onChange={e=>setDraft({...data,plan:{...data.plan,entry_service:e.target.value}})}><option value="">请选择</option>{data.plan.services.map(s=><option key={s.id}>{s.id}</option>)}</select></label><label>入口路径<input value={data.plan.entry_path} onChange={e=>setDraft({...data,plan:{...data.plan,entry_path:e.target.value}})}/></label>
        <label>页面标识文本<input value={data.plan.page_text} onChange={e=>setDraft({...data,plan:{...data.plan,page_text:e.target.value}})}/></label><label>页面定位器<input value={data.plan.page_selector} onChange={e=>setDraft({...data,plan:{...data.plan,page_selector:e.target.value}})}/></label>
        {data.plan.blockers.map((b,i)=><label className="runtime-check full" key={i}><input type="checkbox" checked={false} onChange={()=>setDraft({...data,plan:{...data.plan,blockers:data.plan.blockers.filter((_,j)=>i!==j)}})}/>已处理：{b}</label>)}
      </>:<p>{data.plan.entry_service} {data.plan.entry_path} · {data.plan.page_text||data.plan.page_selector||"尚未确认"}</p>}</div></section>
    </>:task.features.filter(f=>f.selected).map(f=>{const rule=data.execution.features[f.id]||emptyFeature();const update=(patch:Partial<typeof rule>)=>setDraft({...data,execution:{features:{...data.execution.features,[f.id]:{...rule,...patch}}}});return <section className="runtime-service" key={f.id}><h3>{f.title}</h3>{draft?<>
      <fieldset><legend>前置功能</legend>{task.features.filter(other=>other.selected&&other.id!==f.id).map(other=><label className="runtime-check" key={other.id}><input type="checkbox" checked={rule.depends_on.includes(other.id)} onChange={e=>update({depends_on:e.target.checked?[...rule.depends_on,other.id]:rule.depends_on.filter(id=>id!==other.id)})}/>{other.title}</label>)}</fieldset>
      <fieldset><legend>确认上传文件</legend>{data.files.map(file=><label className="runtime-check" key={file.id}><input type="checkbox" checked={rule.file_ids.includes(file.id)} onChange={e=>update({file_ids:e.target.checked?[...rule.file_ids,file.id]:rule.file_ids.filter(id=>id!==file.id)})}/>{file.name} · {file.purpose}</label>)}</fieldset>
      <div className="form-grid"><label>成功提示<input value={rule.success_text} onChange={e=>update({success_text:e.target.value})}/></label><label>下载扩展名<input value={rule.download_extension} onChange={e=>update({download_extension:e.target.value})}/></label><label>成功接口路径<input value={rule.success_response?.path||""} onChange={e=>update({success_response:e.target.value?{path:e.target.value,method:rule.success_response?.method||"POST",json_equals:rule.success_response?.json_equals||{ok:true}}:null})}/></label>{rule.success_response&&<><label>请求方法<select value={rule.success_response.method} onChange={e=>update({success_response:{...rule.success_response!,method:e.target.value}})}>{["POST","PUT","PATCH","GET","DELETE"].map(m=><option key={m}>{m}</option>)}</select></label><JsonEditor label="接口成功字段" value={rule.success_response.json_equals} onChange={v=>update({success_response:{...rule.success_response!,json_equals:v as Record<string,unknown>}})} onError={setLocalError}/></>}</div>
    </>:<p>前置：{rule.depends_on.map(id=>task.features.find(x=>x.id===id)?.title||id).join("、")||"无"}<br/>文件：{rule.file_ids.map(id=>data.files.find(x=>x.id===id)?.name||"文件已删除").join("、")||"无"}<br/>验证：{rule.success_text||rule.success_response?.path||rule.download_extension||"页面操作结果"}</p>}</section>})}
    <div className="runtime-actions">{draft?<><button className="button secondary" disabled={busy} onClick={()=>{setDraft(null);setLocalError("");}}><X size={16}/>取消</button><button className="button primary" disabled={busy||!!localError||data.revision!==query.data?.revision} onClick={()=>mutate(path,{revision:data.revision,value:mode==="plan"?data.plan:data.execution},"PUT")}><Save size={16}/>保存</button></>:<button className="button primary" disabled={busy||!!data.confirmations[mode]||data.blockers.length>0} onClick={()=>mutate(`${path}/confirm`,{revision:data.revision})}><Check size={16}/>{data.confirmations[mode]?"已确认":mode==="plan"?"确认运行方案":"确认文件用途与依赖"}</button>}</div>
    {mode==="plan"&&<>
      <section className="form-section"><h3>测试文件</h3>{data.files.map(f=><div className="runtime-file" key={f.id}><div><strong>{f.name}</strong><p>{f.purpose} · {Math.ceil(f.size/1024)} KB</p></div><a className="icon-button" title="下载测试文件" href={`/api/tasks/${task.id}/test-files/${f.id}`}><Download size={16}/></a><button className="icon-button" title="删除测试文件" disabled={busy||!!draft} onClick={()=>remove.mutate(f.id)}><Trash2 size={16}/></button></div>)}
        <div className="form-grid"><label>选择文件<input type="file" disabled={busy||!!draft} onChange={e=>setFile(e.target.files?.[0]||null)}/></label><label>文件用途<input value={purpose} disabled={busy||!!draft} onChange={e=>setPurpose(e.target.value)}/></label></div><button className="button secondary" disabled={busy||!!draft||!file||!purpose.trim()} onClick={()=>upload.mutate()}><Upload size={16}/>上传测试文件</button>
      </section><details className="form-section"><summary>测试账号</summary><div className="form-grid"><label>账号<input autoComplete="off" value={credentials.username} onChange={e=>setCredentials({...credentials,username:e.target.value})}/></label><label>密码<input type="password" autoComplete="new-password" value={credentials.password} onChange={e=>setCredentials({...credentials,password:e.target.value})}/></label></div><button className="button secondary" disabled={busy||login.isPending} onClick={()=>login.mutate()}><Save size={16}/>保存到当前服务进程</button></details>
    </>}
  </div>;
}

function JsonEditor({label,value,onChange,onError}:{label:string;value:unknown;onChange:(v:unknown)=>void;onError:(s:string)=>void}) {
  const [text,setText]=useState(JSON.stringify(value));
  return <label>{label}<textarea value={text} onChange={e=>{setText(e.target.value);try{onChange(JSON.parse(e.target.value));e.target.setCustomValidity("");onError("");}catch{e.target.setCustomValidity(`${label}格式不合法`);e.target.reportValidity();}}}/></label>;
}

export function RuntimeLogs({task}:{task:Task}) {
  const query=useQuery({queryKey:["runtime",task.id],queryFn:()=>runtimeApi.state(task.id),refetchInterval:3000});
  const [logs,setLogs]=useState<Record<string,string[]>>({});
  const [selected,setSelected]=useState("");
  const run=query.data?.runs[0];const names=Object.keys(run?.data.services||{});const current=selected||names[0]||"runtime";
  const savedLogs=useQuery({queryKey:["service-logs",task.id,run?.id,current],enabled:!!run,queryFn:()=>request<{text:string}>(`/api/tasks/${task.id}/runtime-runs/${run!.id}/logs/${current}`),refetchInterval:3000});
  const decision=useMutation({mutationFn:({id,approved}:{id:string;approved:boolean})=>api.approval(task.id,id,approved)});
  useEffect(()=>{const events=new EventSource(`/api/tasks/${task.id}/events`);events.addEventListener("service_log",event=>{const x=JSON.parse((event as MessageEvent).data);setLogs(previous=>({...previous,[x.service]:[...(previous[x.service]||[]),x.message].slice(-200)}));});return()=>events.close();},[task.id]);
  return <section className="runtime-log-section"><h3>服务运行记录</h3>{run&&<p>{run.status} · 修订 {run.revision}</p>}<div className="runtime-actions">{names.map(name=><button className="button secondary" key={name} onClick={()=>setSelected(name)}>{name} · {run?.data.services?.[name].status}</button>)}</div><pre>{savedLogs.data?.text||logs[current]?.join("\n")||run?.data.error||"暂无服务日志"}</pre>{task.approvals.filter(a=>a.status==="pending").map(a=><div className="runtime-service" key={a.id}><p>{a.reason}</p><div className="runtime-actions"><button className="button secondary" disabled={decision.isPending} onClick={()=>decision.mutate({id:a.id,approved:false})}><X size={16}/>拒绝</button><button className="button primary" disabled={decision.isPending} onClick={()=>decision.mutate({id:a.id,approved:true})}><Check size={16}/>批准</button></div></div>)}</section>;
}
