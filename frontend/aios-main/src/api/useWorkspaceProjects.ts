import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgentSpecDto, AuditEventDto, SessionStateDto, SessionSummaryDto, SpecVersionDto, WorkItemDto } from './dto';
import { normalizeNetworkError } from './errors';
import { getSessionState, listAgentSpecs, listEvents, listSessions, listSpecs, listWorkItems } from './sessions';

const ACTIVE_SESSION_KEY = 'firstflight.active-session-id';

export type ResourceBundle = {
  specs: SpecVersionDto[];
  workItems: WorkItemDto[];
  agentSpecs: AgentSpecDto[];
  events: AuditEventDto[];
};
export type WorkspaceProject = { state: SessionStateDto; resources: ResourceBundle };
export const emptyResources: ResourceBundle = { specs: [], workItems: [], agentSpecs: [], events: [] };

export function projectSpec(project?: WorkspaceProject) {
  return project?.resources.specs.find((spec) => spec.id === project.state.current_spec_version_id)
    ?? [...(project?.resources.specs ?? [])].sort((left, right) => right.revision - left.revision)[0];
}

export function projectTitle(project: WorkspaceProject) {
  return project.resources.workItems.find((item) => item.kind === 'ROOT')?.title || project.state.session_id;
}

export function useWorkspaceProjects() {
  const [catalog, setCatalog] = useState<SessionSummaryDto[]>([]);
  const [projects, setProjects] = useState<Record<string, WorkspaceProject>>({});
  const [activeSessionId, setActiveSessionId] = useState<string | null>(() => localStorage.getItem(ACTIVE_SESSION_KEY));
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const loadVersions = useRef(new Map<string, number>());

  const selectSession = useCallback((sessionId: string | null) => {
    setActiveSessionId(sessionId);
    if (sessionId) localStorage.setItem(ACTIVE_SESSION_KEY, sessionId);
    else localStorage.removeItem(ACTIVE_SESSION_KEY);
  }, []);

  const updateSessionState = useCallback((state: SessionStateDto) => {
    setProjects((previous) => {
      if ((previous[state.session_id]?.state.state_version ?? -1) > state.state_version) {
        return previous;
      }
      return { ...previous, [state.session_id]: {
        state, resources: previous[state.session_id]?.resources ?? emptyResources,
      } };
    });
  }, []);

  const refreshResources = useCallback(async (sessionId: string, signal?: AbortSignal) => {
    const version = (loadVersions.current.get(sessionId) ?? 0) + 1;
    loadVersions.current.set(sessionId, version);
    let sessionMissing = false;
    try {
      const state = await getSessionState(sessionId, signal).catch((reason) => {
        const error = normalizeNetworkError(reason);
        sessionMissing = error.status === 404 || error.code === 'NOT_FOUND';
        throw reason;
      });
      const [specs, workItems, agentSpecs, events] = await Promise.all([
        listSpecs(sessionId, signal), listWorkItems(sessionId, signal),
        listAgentSpecs(sessionId, signal), listEvents(sessionId, signal),
      ]);
      const project = { state, resources: { specs, workItems, agentSpecs, events } };
      if (!signal?.aborted && loadVersions.current.get(sessionId) === version) {
        const summary = {
          session_id: sessionId, project_id: state.project_id,
          root_work_item_id: workItems.find((item) => item.kind === 'ROOT')?.id ?? null,
          title: projectTitle(project),
        };
        setCatalog((previous) => previous.some((item) => item.session_id === sessionId)
          ? previous.map((item) => item.session_id === sessionId ? summary : item)
          : [...previous, summary]);
        setProjects((previous) => {
          if ((previous[sessionId]?.state.state_version ?? -1) > state.state_version) return previous;
          return { ...previous, [sessionId]: project };
        });
        setLoadErrors((previous) => { const next = { ...previous }; delete next[sessionId]; return next; });
      }
      return project;
    } catch (reason) {
      const error = normalizeNetworkError(reason);
      if (signal?.aborted || error.code === 'REQUEST_ABORTED' || loadVersions.current.get(sessionId) !== version) throw reason;
      if (sessionMissing || error.status === 403) {
        setProjects((previous) => { const next = { ...previous }; delete next[sessionId]; return next; });
        setActiveSessionId((current) => current === sessionId ? null : current);
        if (localStorage.getItem(ACTIVE_SESSION_KEY) === sessionId) localStorage.removeItem(ACTIVE_SESSION_KEY);
      }
      if (sessionMissing) {
        setCatalog((previous) => previous.filter((item) => item.session_id !== sessionId));
        setLoadErrors((previous) => { const next = { ...previous }; delete next[sessionId]; return next; });
      } else {
        setLoadErrors((previous) => ({ ...previous, [sessionId]: `项目 ${sessionId} 加载失败：${error.message}` }));
      }
      throw reason;
    }
  }, []);

  const refreshAllProjects = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const summaries = await listSessions(signal);
      if (signal?.aborted) return;
      setCatalog(summaries);
      const ids = new Set(summaries.map((item) => item.session_id));
      setProjects((previous) => Object.fromEntries(Object.entries(previous).filter(([id]) => ids.has(id))));
      setLoadErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([id]) => ids.has(id))));
      setActiveSessionId((current) => {
        if (current && !ids.has(current)) {
          if (localStorage.getItem(ACTIVE_SESSION_KEY) === current) localStorage.removeItem(ACTIVE_SESSION_KEY);
          return null;
        }
        return current;
      });
      // Bound concurrent resource loads while still including the complete catalog.
      const pending = [...ids];
      const worker = async () => {
        while (pending.length && !signal?.aborted) {
          const id = pending.shift()!;
          await refreshResources(id, signal).catch(() => undefined);
        }
      };
      await Promise.all(Array.from({ length: Math.min(4, pending.length) }, worker));
    } catch (reason) {
      const error = normalizeNetworkError(reason);
      if (!signal?.aborted && error.code !== 'REQUEST_ABORTED') {
        setLoadErrors((previous) => ({ ...previous, catalog: `项目列表加载失败：${error.message}` }));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [refreshResources]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshAllProjects(controller.signal);
    return () => controller.abort();
  }, [refreshAllProjects]);

  const activeProject = activeSessionId ? projects[activeSessionId] : undefined;
  return {
    projects, catalog, activeSessionId, state: activeProject?.state ?? null,
    resources: activeProject?.resources ?? emptyResources,
    loading, loadErrors, selectSession, updateSessionState, refreshResources, refreshAllProjects,
  };
}
