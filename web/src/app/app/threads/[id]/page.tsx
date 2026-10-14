/** Thread — the full story of one email: state, path, outcome, proposal, audit trail, checkpoints, trace link,
 *  feedback, and the recovery action for a cut-off run (design.md §14). */
import Link from "next/link";
import { ContinueButton } from "@/components/continue-button";
import { FeedbackBar } from "@/components/feedback-bar";
import { CitationChips } from "@/components/citation-chip";
import { Card, Chip, ErrorState, PageHeader, SectionTitle, StatusPill } from "@/components/ui";
import { traceUrl, tryFetch } from "@/lib/api";
import { fmtTime } from "@/lib/dates";
import { requireProducer } from "@/lib/session";

interface ThreadPayload {
  thread_id: string;
  status: string;
  next: string[];
  paused: boolean;
  interrupt: Record<string, unknown> | null;
  values: Record<string, unknown>;
  checkpoint_id: string | null;
  metadata: Record<string, unknown> | null;
}
interface AuditRow {
  id: number;
  at: string;
  actor: string;
  action: string;
  ref: Record<string, unknown> | null;
}
interface HistoryRow {
  checkpoint_id: string | null;
  next: string[];
  step: number | null;
  path: string[] | string | null;
}

/** `path` in the state is the list of nodes visited; render it as intake → classify → … */
function pathText(p: unknown): string | null {
  if (Array.isArray(p)) return p.length ? p.join(" → ") : null;
  return typeof p === "string" && p ? p : null;
}

function val<T>(v: Record<string, unknown>, k: string): T | undefined {
  return v[k] as T | undefined;
}

export default async function ThreadPage({ params }: { params: Promise<{ id: string }> }) {
  const user = await requireProducer();
  const { id } = await params;
  const threadId = decodeURIComponent(id);
  const enc = encodeURIComponent(threadId);
  const [thread, audit, history] = await Promise.all([
    tryFetch<ThreadPayload>(`/threads/${enc}`, user.name),
    tryFetch<{ items: AuditRow[] }>(`/threads/${enc}/audit`, user.name),
    tryFetch<HistoryRow[]>(`/threads/${enc}/history?limit=20`, user.name),
  ]);
  if (thread.error || !thread.data) return <ErrorState>Could not load thread: {thread.error}</ErrorState>;
  const t = thread.data;
  const v = t.values ?? {};
  const email = val<Record<string, string>>(v, "email");
  const proposal = val<Record<string, unknown>>(v, "proposal");
  const decision = val<Record<string, unknown>>(v, "decision");
  const brief = val<Record<string, unknown>>(v, "analyst_brief");
  const classification = val<Record<string, unknown>>(v, "classification");
  const auditRows = audit.data?.items ?? [];
  // one link per turn: the run (proposal_staged), the decision (…_executed / proposal_rejected), a failed turn
  const turns: { id: string; label: string }[] = [];
  for (const a of auditRows) {
    const id = a.ref?.trace_id as string | undefined;
    if (id && !turns.some((x) => x.id === id)) turns.push({ id, label: a.action });
  }
  const traceId = turns.length ? turns[turns.length - 1]!.id : null; // the latest turn — where feedback lands
  const trace = traceUrl(traceId);
  const cutOff = t.status === "running" && !t.paused && t.next.length > 0;

  return (
    <>
      <PageHeader
        title={email?.subject || threadId}
        subtitle={email ? `${email.from_name || email.from_email} · ${email.from_email}` : "thread"}
        action={
          <div className="flex flex-wrap items-center justify-center gap-2">
            <StatusPill status={t.status} />
            {pathText(v.path) && <Chip accent>path · {pathText(v.path)}</Chip>}
            {typeof v.outcome === "string" && <Chip>outcome · {v.outcome}</Chip>}
            {t.paused && (
              <Link href="/app/approve" className="text-[13px] text-accent hover:underline">
                decide on Approve →
              </Link>
            )}
            {cutOff && <ContinueButton threadId={threadId} />}
          </div>
        }
      />

      {typeof v.error === "string" && v.error && (
        <div className="mb-3 rounded-btn bg-warning-bg px-3 py-2 text-[13px] text-warning-fg">
          Degradation on this turn: {v.error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Card>
          <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted">Inbound email</div>
          <div className="whitespace-pre-wrap text-[13px] leading-relaxed">{email?.body || "—"}</div>
        </Card>
        <Card>
          <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted">Classification</div>
          {classification ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
              {Object.entries(classification).map(([k, x]) => (
                <div key={k} className="contents">
                  <dt className="font-mono text-muted">{k}</dt>
                  <dd>{typeof x === "object" ? JSON.stringify(x) : String(x)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="text-[13px] text-muted">not yet classified</div>
          )}
          {brief && (
            <>
              <div className="mb-1 mt-3 text-[11px] font-bold uppercase tracking-wide text-muted">Analyst brief</div>
              <div className="whitespace-pre-wrap text-[13px]">{String(brief.summary ?? brief.text ?? JSON.stringify(brief))}</div>
              <div className="mt-2">
                <CitationChips citations={(brief.citations as string[]) ?? []} />
              </div>
            </>
          )}
        </Card>
      </div>

      {proposal && (
        <>
          <SectionTitle>Proposal</SectionTitle>
          <Card>
            <div className="flex items-center justify-between">
              <Chip accent>{String(proposal.kind ?? "").replace(/_/g, " ")}</Chip>
              {decision && <StatusPill status={String(decision.action ?? "decided")} />}
            </div>
            {typeof proposal.owner_brief === "string" && (
              <div className="mt-2 whitespace-pre-wrap text-[13px]">{proposal.owner_brief}</div>
            )}
            <pre className="mt-2 overflow-x-auto rounded-btn bg-chip p-2.5 text-[12px]">{JSON.stringify(proposal.payload ?? {}, null, 2)}</pre>
            {decision && (
              <div className="mt-2 text-[12.5px] text-muted">
                decided by {String(decision.by ?? "?")}: {String(decision.action)}
                {decision.reason ? ` — ${String(decision.reason)}` : ""}
              </div>
            )}
          </Card>
        </>
      )}

      <SectionTitle>Audit trail</SectionTitle>
      {audit.error ? (
        <ErrorState>{audit.error}</ErrorState>
      ) : (
        <div className="card p-0">
          <table className="w-full text-[13px]">
            <thead className="text-left text-[11.5px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">When</th>
                <th className="px-3 py-2">Actor</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Ref</th>
              </tr>
            </thead>
            <tbody>
              {auditRows.map((a) => (
                <tr key={a.id} className="border-t border-line align-top">
                  <td className="px-3 py-2 whitespace-nowrap">{fmtTime(a.at)}</td>
                  <td className="px-3 py-2">{a.actor}</td>
                  <td className="px-3 py-2 font-medium">{a.action}</td>
                  <td className="px-3 py-2 font-mono text-[11.5px] text-muted">{a.ref ? JSON.stringify(a.ref) : ""}</td>
                </tr>
              ))}
              {auditRows.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-muted">No audit entries yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <SectionTitle>Trace &amp; feedback</SectionTitle>
      <Card>
        <div className="flex flex-wrap items-center gap-3 text-[13px]">
          {turns.length === 0 ? (
            <span className="text-muted">No trace recorded yet.</span>
          ) : (
            turns.map((turn) => {
              const href = traceUrl(turn.id);
              return href ? (
                <a key={turn.id} href={href} target="_blank" rel="noreferrer" className="text-accent hover:underline" data-testid="trace-link">
                  Open trace in LangSmith ↗ <span className="font-mono text-[11px] text-muted">{turn.label}</span>
                </a>
              ) : (
                <span key={turn.id} className="font-mono text-[12px]">
                  {turn.label} trace {turn.id}
                </span>
              );
            })
          )}
          <span className="text-muted">·</span>
          <span className="text-muted">Rate this thread (saved as LangSmith feedback on the trace):</span>
        </div>
        <FeedbackBar threadId={threadId} traceId={traceId} />
      </Card>

      <SectionTitle>Checkpoints</SectionTitle>
      {history.error ? (
        <ErrorState>{history.error}</ErrorState>
      ) : (
        <div className="card p-0">
          <table className="w-full text-[12.5px]">
            <thead className="text-left text-[11.5px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">Step</th>
                <th className="px-3 py-2">Next</th>
                <th className="px-3 py-2">Path</th>
                <th className="px-3 py-2">Checkpoint</th>
              </tr>
            </thead>
            <tbody>
              {(history.data ?? []).map((h, i) => (
                <tr key={h.checkpoint_id ?? i} className="border-t border-line">
                  <td className="px-3 py-1.5">{h.step ?? "—"}</td>
                  <td className="px-3 py-1.5">{h.next.join(", ") || "(end)"}</td>
                  <td className="px-3 py-1.5">{pathText(h.path) ?? "—"}</td>
                  <td className="px-3 py-1.5 font-mono text-[11px] text-muted">{h.checkpoint_id?.slice(0, 18)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
