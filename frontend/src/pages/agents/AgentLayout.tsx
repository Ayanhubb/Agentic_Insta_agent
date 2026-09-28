import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { ErrorState, LoadingState } from "../../components/ui/States";
import { agentsApi, userMessageFor } from "../../services/api";
import type { PlatformAgent } from "../../types/agents";

const SECTIONS = [
  { to: "", label: "Overview", end: true },
  { to: "workflow", label: "Workflow" },
  { to: "tools", label: "Tools" },
  { to: "models", label: "Models" },
  { to: "assets", label: "Assets" },
  { to: "knowledge", label: "Knowledge" },
  { to: "triggers", label: "Triggers" },
  { to: "approval", label: "Approval" },
  { to: "executions", label: "Executions" },
];

interface AgentConfigValue {
  agent: PlatformAgent;
  setAgent: (agent: PlatformAgent) => void;
}

const AgentConfigContext = createContext<AgentConfigValue | null>(null);

export function useAgentConfig(): AgentConfigValue {
  const value = useContext(AgentConfigContext);
  if (!value) {
    throw new Error("Agent pages must render inside the agent layout.");
  }
  return value;
}

export function AgentLayout() {
  const { agentId = "" } = useParams();
  const [agent, setAgent] = useState<PlatformAgent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setAgent(null);
    agentsApi
      .get(agentId)
      .then((next) => {
        if (!cancelled) setAgent(next);
      })
      .catch((err) => {
        if (!cancelled) setError(userMessageFor(err));
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  if (error) return <ErrorState message={error} />;
  if (!agent) return <LoadingState label="Loading agent…" />;

  return (
    <AgentConfigContext.Provider value={{ agent, setAgent }}>
      <div className="page">
        <div className="page-header">
          <div>
            <h1>{agent.display_name}</h1>
            <p className="lede">
              {agent.writable
                ? "Configure registered providers, tools, and policies. API keys stay on the server."
                : "The Instagram studio remains this agent's interface. These settings are read-only."}
            </p>
          </div>
        </div>
        <nav className="section-subnav" aria-label="Agent configuration">
          {SECTIONS.map((section) => (
            <NavLink key={section.label} to={section.to} end={section.end}>
              {section.label}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
    </AgentConfigContext.Provider>
  );
}
