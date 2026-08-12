export type Status =
  | "uploaded" | "analyzing" | "awaiting_review" | "ready" | "queued" | "installing"
  | "starting" | "authenticating" | "exploring" | "review_ready" | "generating"
  | "completed" | "paused" | "failed" | "cancelled";

export interface LaunchPlan {
  project_type: string;
  working_directory: string;
  install_command: string[];
  start_command: string[];
  environment: Record<string, string>;
  detected_files: string[];
  confirmed: boolean;
}

export interface Screenshot {
  id: string;
  url: string;
  included: boolean;
}

export interface Step {
  id: string;
  position: number;
  action: string;
  instruction: string;
  url?: string;
  result: string;
  screenshot?: Screenshot;
}

export interface Feature {
  id: string;
  title: string;
  entry_path: string;
  goal: string;
  position: number;
  selected: boolean;
  status: string;
  error?: string;
  steps: Step[];
}

export interface Approval {
  id: string;
  action: Record<string, unknown>;
  reason: string;
  status: string;
}

export interface Artifact {
  id: string;
  kind: string;
  name: string;
  size: number;
  url: string;
}

export interface TaskSummary {
  id: string;
  name: string;
  status: Status;
  updated_at: string;
}

export interface Task extends TaskSummary {
  browser_mode: "headed" | "headless";
  start_url?: string;
  error?: string;
  launch_plan?: LaunchPlan;
  features: Feature[];
  approvals: Approval[];
  artifacts: Artifact[];
}
