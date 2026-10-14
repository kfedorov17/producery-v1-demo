import "server-only";
/** The ONE way the UI reaches the agent API: server-side only, bearer token from the environment, the signed-in
 *  producer's name in `x-producery-user`. Used by server components directly and by the /api/ui proxy for client
 *  components. Nothing in the browser bundle imports this module. */

const AGENT_URL = (process.env.AGENT_URL ?? "http://localhost:8010").replace(/\/$/, "");

export class AgentError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function agentFetch<T = unknown>(
  path: string,
  init: { method?: string; body?: unknown; user?: string; timeoutMs?: number } = {},
): Promise<T> {
  const token = process.env.AGENT_API_TOKEN;
  if (!token) throw new AgentError(500, "AGENT_API_TOKEN is not configured for the web service");
  const res = await fetch(`${AGENT_URL}${path}`, {
    method: init.method ?? "GET",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      "x-producery-user": init.user ?? "web",
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
    signal: AbortSignal.timeout(init.timeoutMs ?? 30_000),
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text.slice(0, 300) };
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    throw new AgentError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail ?? data));
  }
  return data as T;
}

/** Safe wrapper for server components: a failing agent call renders an error state instead of a 500 page. */
export async function tryFetch<T>(path: string, user?: string): Promise<{ data: T | null; error: string | null }> {
  try {
    return { data: await agentFetch<T>(path, { user }), error: null };
  } catch (e) {
    return { data: null, error: e instanceof Error ? e.message : String(e) };
  }
}

/** Base of a LangSmith trace link for this workspace/project, or null when the ids are not configured. */
export function traceBase(): string | null {
  const org = process.env.LANGSMITH_ORG_ID;
  const project = process.env.LANGSMITH_PROJECT_ID;
  return org && project ? `https://smith.langchain.com/o/${org}/projects/p/${project}/r/` : null;
}

export function traceUrl(traceId: string | null | undefined): string | null {
  const base = traceBase();
  return traceId && base ? `${base}${traceId}?poll=true` : null;
}
