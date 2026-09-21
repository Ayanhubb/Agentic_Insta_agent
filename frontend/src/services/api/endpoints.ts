/**
 * HTTP paths taken from FastAPI routers:
 *   api/routes.py
 *   api/auth_routes.py
 *   api/platform_routes.py
 *   api/generation.py
 *   api/instagram_account_routes.py
 */
export const API = {
  health: "/api/v1/health",
  dashboard: "/api/v1/dashboard",
  settings: "/api/v1/settings",
  auth: {
    register: "/api/v1/auth/register",
    login: "/api/v1/auth/login",
    logout: "/api/v1/auth/logout",
    me: "/api/v1/auth/me",
    changePassword: "/api/v1/auth/change-password",
  },
  instagram: {
    publish: "/api/v1/instagram/publish",
    status: "/api/v1/instagram/status",
    connect: "/api/v1/instagram/connect",
    disconnect: "/api/v1/instagram/disconnect",
  },
  tasks: {
    one: (taskId: string) => `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    events: (taskId: string) => `/api/v1/tasks/${encodeURIComponent(taskId)}/events`,
    owned: (taskId: string) => `/api/v1/tasks/${encodeURIComponent(taskId)}/owned`,
  },
  media: (filename: string) => `/api/v1/media/${encodeURIComponent(filename)}`,
  mediaGenerated: (imageId: string) => `/api/v1/media/generated/${encodeURIComponent(imageId)}`,
  generation: {
    list: "/api/v1/generation",
    create: "/api/v1/generation",
    one: (id: string) => `/api/v1/generation/${encodeURIComponent(id)}`,
    reject: (id: string) => `/api/v1/generation/${encodeURIComponent(id)}/reject`,
    regenerate: (id: string) => `/api/v1/generation/${encodeURIComponent(id)}/regenerate`,
    approve: (id: string) => `/api/v1/generation/${encodeURIComponent(id)}/approve`,
  },
  posts: "/api/v1/posts",
  automation: {
    get: "/api/v1/automation",
    put: "/api/v1/automation",
    runNow: "/api/v1/automation/run-now",
  },
  festivals: {
    list: "/api/v1/festivals",
    campaigns: "/api/v1/festivals/campaigns",
    settings: "/api/v1/festivals/settings",
  },
  business: "/api/v1/business",
  adminUsers: "/api/v1/admin/users",
} as const;
