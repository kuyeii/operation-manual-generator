import { request } from "./api";

export interface Service {
  id: string; framework: string; runtime: string; directory: string; image: string;
  install: string[][]; build: string[][]; command: string[]; environment: Record<string,string>;
  depends_on: string[]; port: number; health_path: string; dockerfile: string; embed_frontend: string | null; evidence: string[];
}
export interface RuntimePlan { services: Service[]; entry_service: string; entry_path: string; page_text: string; page_selector: string; blockers: string[]; }
export interface FeatureExecution { depends_on: string[]; file_ids: string[]; success_text: string; success_response: { path: string; method: string; json_equals: Record<string,unknown> } | null; download_extension: string; }
export interface RuntimeState {
  revision: number; plan: RuntimePlan; execution: {features: Record<string,FeatureExecution>};
  confirmations: Record<string,boolean>; blockers: string[];
  files: {id: string; name: string; purpose: string; size: number; media_type: string}[];
  runs: {id: string; run_id: string; status: string; revision: number; created_at: string; data: {services?: Record<string,{status:string;url?:string}>;error?:string}}[];
}
export const runtimeApi = {
  state: (id:string) => request<RuntimeState>(`/api/tasks/${id}/runtime-plan`),
  mutate: (id:string,path:string,method:string,value:unknown) => request<RuntimeState>(`/api/tasks/${id}/${path}`,{method,headers:{"Content-Type":"application/json"},body:JSON.stringify(value)}),
  upload: (id:string,data:FormData) => request(`/api/tasks/${id}/test-files`,{method:"POST",body:data}),
  remove: (id:string,file:string,revision:number) => request(`/api/tasks/${id}/test-files/${file}?revision=${revision}`,{method:"DELETE"}),
};
export const emptyFeature = (): FeatureExecution => ({depends_on:[],file_ids:[],success_text:"",success_response:null,download_extension:""});
