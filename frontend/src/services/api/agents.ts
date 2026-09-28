import { receivePublic } from "../../lib/agentConfig";
import type {
  AgentExecution,
  PlatformAgent,
} from "../../types/agents";
import { request } from "./client";
import { API } from "./endpoints";

function one(id: string, suffix = ""): string {
  return `${API.platformAgents.one(id)}${suffix}`;
}

function agentOf(payload: unknown): PlatformAgent {
  const body = receivePublic<{ agent: PlatformAgent }>(payload);
  return body.agent;
}

export const agentsApi = {
  list(): Promise<PlatformAgent[]> {
    return request<unknown>(API.platformAgents.list).then((payload) => {
      const body = receivePublic<{ agents?: PlatformAgent[] }>(payload);
      return body.agents ?? [];
    });
  },

  create(input: { key: string; display_name: string; description: string }): Promise<PlatformAgent> {
    return request<unknown>(API.platformAgents.list, {
      method: "POST",
      body: JSON.stringify(input),
    }).then(agentOf);
  },

  get(id: string): Promise<PlatformAgent> {
    return request<unknown>(one(id)).then(agentOf);
  },

  update(id: string, input: object): Promise<PlatformAgent> {
    return request<unknown>(one(id), {
      method: "PATCH",
      body: JSON.stringify(input),
    }).then(agentOf);
  },

  disable(id: string): Promise<PlatformAgent> {
    return request<unknown>(one(id, "/disable"), { method: "POST" }).then(agentOf);
  },

  retire(id: string): Promise<PlatformAgent> {
    return request<unknown>(one(id), { method: "DELETE" }).then(agentOf);
  },

  executions(id: string): Promise<AgentExecution[]> {
    return request<unknown>(one(id, "/executions")).then((payload) => {
      const body = receivePublic<{ executions?: AgentExecution[] }>(payload);
      return body.executions ?? [];
    });
  },
};
