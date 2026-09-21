/** Types matching documented FastAPI / V2 domain contracts. */

export type PostStatus =
  | "GENERATED"
  | "APPROVED"
  | "PUBLISHING"
  | "PUBLISHED"
  | "FAILED"
  | "AMBIGUOUS_PUBLICATION";

export type ImageSource = "USER_PROMPT" | "DAILY_AUTOMATION" | "FESTIVAL_AUTOMATION";

export type TaskStatus =
  | "pending"
  | "planning"
  | "validating"
  | "preparing"
  | "uploading"
  | "creating_media"
  | "publishing"
  | "verifying"
  | "completed"
  | "failed";

export interface PublicError {
  code: string;
  message: string;
}

export interface User {
  id: string;
  email: string;
  is_admin: boolean;
  is_active?: boolean;
  must_change_password: boolean;
}

export interface AuthResponse {
  user: User;
  access_token?: string;
  token?: string;
  token_type?: string;
}

export interface TraceStep {
  step: number | string;
  tool: string;
  purpose?: string;
  status: "pending" | "running" | "success" | "failed" | "skipped";
  duration_ms?: number | null;
  error_code?: string | null;
  decision?: string | null;
  observation_summary?: Record<string, unknown>;
  label?: string;
}

export interface TaskStatusPayload {
  success: boolean;
  task_id: string;
  request_id?: string | null;
  platform?: string;
  status: TaskStatus | string;
  current_step?: string | null;
  instagram_media_id?: string | null;
  image_url?: string | null;
  permalink?: string | null;
  message?: string | null;
  error?: PublicError | null;
  steps?: TraceStep[];
  execution_trace?: TraceStep[];
}

export interface PublishResponse {
  success: boolean;
  task_id: string;
  platform?: string;
  status: string;
  instagram_media_id?: string | null;
  message?: string | null;
  error?: PublicError | null;
  steps?: TraceStep[];
}

export interface GeneratedImage {
  id: string;
  original_prompt?: string | null;
  enhanced_prompt?: string | null;
  model?: string | null;
  provider?: string | null;
  filename?: string | null;
  mime_type?: string | null;
  width?: number | null;
  height?: number | null;
  generation_status?: string | null;
  approval_status?: string | null;
  publication_status?: string | null;
  source?: ImageSource | string | null;
  preview_url?: string | null;
  image_url?: string | null;
  created_at?: string | null;
  approved_at?: string | null;
}

export interface ContentJob {
  id: string;
  status?: string | null;
  original_prompt?: string | null;
  prompt?: string | null;
  enhanced_prompt?: string | null;
  image_prompt?: string | null;
  caption?: string | null;
  preview_url?: string | null;
  image_url?: string | null;
  generated_image_id?: string | null;
  image_id?: string | null;
  task_id?: string | null;
  source?: string | null;
  created_at?: string | null;
}

export interface InstagramPost {
  id: string;
  generated_image_id?: string | null;
  instagram_media_id?: string | null;
  permalink?: string | null;
  status: PostStatus | string;
  post_type?: string | null;
  source?: string | null;
  published_at?: string | null;
  created_at?: string | null;
  error?: string | PublicError | null;
  preview_url?: string | null;
  image_url?: string | null;
}

export interface AutomationSettings {
  daily_enabled?: boolean;
  daily_posts_per_day?: number;
  daily_post_time?: string;
  festival_enabled?: boolean;
  festival_posts_per_festival?: number;
  auto_daily_publish?: boolean;
  auto_festival_publish?: boolean;
  auto_publish?: boolean;
  timezone?: string;
  next_run_at?: string | null;
}

export interface Festival {
  id?: string;
  festival_name?: string;
  name?: string;
  festival_date?: string;
  date?: string;
  starts_on?: string;
  year?: number;
  region?: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
}

export interface FestivalCampaign {
  id: string;
  festival_name?: string;
  name?: string;
  festival_date?: string;
  date?: string;
  year?: number;
  required_posts?: number;
  generated_posts?: number;
  published_posts?: number;
  remaining_posts?: number;
  enabled?: boolean;
  status?: string;
}

export interface FestivalSettings {
  festival_enabled?: boolean;
  festival_posts_per_festival?: number;
  auto_festival_publish?: boolean;
}

export interface BusinessProfile {
  business_name?: string;
  business_type?: string;
  business_category?: string;
  description?: string;
  target_audience?: string;
  location?: string;
  brand_style?: string;
  preferred_language?: string;
  products?: string;
  services?: string;
}

export interface InstagramStatus {
  connected?: boolean;
  status?: string;
  username?: string | null;
  instagram_account_id?: string | null;
  ig_user_id?: string | null;
  connected_at?: string | null;
  authorization_url?: string | null;
}

export interface HealthResponse {
  status: string;
}
