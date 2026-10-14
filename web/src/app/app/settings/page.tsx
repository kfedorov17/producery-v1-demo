/** Settings — who you are, what the crew is running (prompt pins, harness profiles, tools, model door), service
 *  readiness, sign out (design.md §14). Read-only: configuration is code, promoted through the registry loop. */
import { SignOutButton } from "@/components/sign-out-button";
import { Card, Chip, ErrorState, PageHeader, SectionTitle, StatusPill } from "@/components/ui";
import { tryFetch } from "@/lib/api";
import { requireProducer } from "@/lib/session";

interface AgentsPayload {
  graph: Record<string, unknown>;
  agents: Record<string, Record<string, unknown>> | Record<string, unknown>[];
  tools: Record<string, unknown>;
  model_door: Record<string, unknown>;
}

function Dump({ value }: { value: unknown }) {
  return <pre className="overflow-x-auto rounded-btn bg-chip p-2.5 text-[12px] leading-relaxed">{JSON.stringify(value, null, 2)}</pre>;
}

export default async function SettingsPage() {
  const user = await requireProducer();
  const [agents, ready] = await Promise.all([
    tryFetch<AgentsPayload>("/agents", user.name),
    tryFetch<{ ok?: boolean; checks?: Record<string, unknown> } & Record<string, unknown>>("/ready", user.name),
  ]);
  const a = agents.data;
  const agentList = a ? (Array.isArray(a.agents) ? a.agents : Object.entries(a.agents).map(([name, d]) => ({ name, ...d }))) : [];

  return (
    <>
      <PageHeader title="Settings" subtitle="Your account, and what the crew is running. Configuration changes go through the registry and CI, not this screen." />
      <SectionTitle>Account</SectionTitle>
      <Card className="flex items-center justify-between">
        <div>
          <div className="font-medium">{user.displayName}</div>
          <div className="text-[12.5px] text-muted">
            signed in as <span className="font-mono">{user.name}</span> · role {user.role}
          </div>
        </div>
        <SignOutButton />
      </Card>

      <SectionTitle>Service readiness</SectionTitle>
      {ready.error && !ready.data ? (
        <ErrorState>{ready.error}</ErrorState>
      ) : (
        <Card>
          <div className="flex flex-wrap gap-2">
            {Object.entries((ready.data?.checks as Record<string, unknown>) ?? ready.data ?? {}).map(([k, v]) => (
              <span key={k} className="flex items-center gap-1.5 text-[13px]">
                <span className="font-mono">{k}</span>
                <StatusPill status={v === true || v === "ok" ? "ready" : typeof v === "object" ? "fallback" : String(v)} />
              </span>
            ))}
          </div>
        </Card>
      )}

      <SectionTitle>Crew</SectionTitle>
      {agents.error || !a ? (
        <ErrorState>{agents.error}</ErrorState>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {agentList.map((ag, i) => {
            const d = ag as Record<string, unknown>;
            return (
              <Card key={String(d.name ?? i)} data-testid="agent-card">
                <div className="flex items-center justify-between">
                  <div className="font-semibold text-heading-accent">{String(d.name ?? "agent")}</div>
                  {typeof d.harness === "string" && <Chip>{d.harness}</Chip>}
                </div>
                <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12.5px]">
                  {Object.entries(d)
                    .filter(([k]) => k !== "name")
                    .map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt className="font-mono text-muted">{k}</dt>
                        <dd className="break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                      </div>
                    ))}
                </dl>
              </Card>
            );
          })}
          <Card>
            <div className="font-semibold text-heading-accent">Model door</div>
            <Dump value={a.model_door} />
          </Card>
          <Card>
            <div className="font-semibold text-heading-accent">Tools</div>
            <Dump value={a.tools} />
          </Card>
          <Card className="md:col-span-2">
            <div className="font-semibold text-heading-accent">Graph</div>
            <Dump value={a.graph} />
          </Card>
        </div>
      )}
    </>
  );
}
