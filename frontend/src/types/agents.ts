/** Secret-free agent configuration returned by `/api/v1/agents`. */

export type AgentOwner = "system" | "user";
export type AgentStatus = "active" | "paused" | "retired";
export type ModelRole = "content_planning" | "image_generation" | "vision_qa";

export interface ModelBinding {
  role: ModelRole;
  implementation: string;
  model: string;
}

export interface AgentVersionView {
  version_number: number;
  status: "draft" | "published";
  workflow_key: string;
  trigger_key: string;
  approval_policy_id: string;
  tool_names: string[];
  knowledge_sources: string[];
  asset_roles: string[];
  models: ModelBinding[];
  timeout_seconds: number;
}

export interface PlatformAgent {
  key: string;
  display_name: string;
  description: string;
  owner: AgentOwner;
  status: AgentStatus;
  published_version_number: number | null;
  draft_version_number: number | null;
  writable: boolean;
  versions: AgentVersionView[];
}

export interface AgentExecution {
  execution_id: string;
  status: string;
  trigger_key: string;
  workflow_key: string | null;
}
