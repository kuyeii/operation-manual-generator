export type Stage = "business" | "registration" | "sources" | "drafts";
export type Operation = "analyze" | "draft" | "review" | "publish";
export interface Business {
  positioning: string; industry: string; users: string; purpose: string;
  development_environment: string; runtime_environment: string;
  features: { title: string; description: string; evidence_ids: string[] }[];
}
export interface Chapter {
  title: string; paragraphs: string[]; evidence_ids: string[];
  feature_id: string | null; screenshot_ids: string[];
}
export interface SourceFile { id: string; path: string; lines: number; effective_lines: number; excerpt: string; sha256: string }
export interface CopyrightState {
  revision: number; status: "idle" | "running" | "failed"; operation: Operation | null;
  progress: string; error: string | null; confirmations: Partial<Record<Stage, { revision: number; digest: string }>>;
  blockers: string[]; field_labels: Record<string, string>; rules: { baseline: string; notice: string };
  data: {
    business?: Business; registration?: Record<string, string>; sources?: { paths: string[] };
    drafts?: { manual: Chapter[]; design: Chapter[] };
    inventory?: { files: SourceFile[]; excluded: { path: string; reason: string }[]; source_lines: number; languages: string[] };
    review_issues?: string[];
    review_resolutions?: Record<string, { issue: string; note: string; draft_digest: string }>;
  };
  batches: { id: string; revision: number; status: string; error: string | null; created_at: string;
    files: { name: string; kind: string; material: string; formal: boolean; size: number; url: string }[] }[];
}
export interface SourcePreview {
  total_pages: number; selected_lines: number;
  volumes: { name: string; pages: { number: number; rows: { text: string; path: string; line: number }[] }[] }[];
}
