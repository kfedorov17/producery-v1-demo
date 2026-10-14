/** Today — the producer's morning view (design.md §14): what is waiting for a decision, what is running, what
 *  failed and needs a look, and what went out. Reads /inbox and /outbox through the server-side client. */
import Link from "next/link";
import { Button, Card, EmptyState, ErrorState, PageHeader, SectionTitle, StatusPill } from "@/components/ui";
import { tryFetch } from "@/lib/api";
import { fmtTime, isToday } from "@/lib/dates";
import { requireProducer } from "@/lib/session";

interface InboxRow {
  id: string;
  from_email: string;
  from_name: string | null;
  subject: string | null;
  status: string;
  thread_id: string | null;
  received_at: string;
  updated_at: string;
  proposal_kind: string | null;
  proposal_status: string | null;
  proposal_id: string | null;
}
interface OutboxRow {
  id: number;
  proposal_id: string;
  to_email: string;
  subject: string | null;
  sent_at: string;
  thread_id: string;
}

function InboxList({ rows, empty }: { rows: InboxRow[]; empty: string }) {
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>;
  return (
    <div className="flex flex-col gap-2">
      {rows.map((r) => (
        <Card key={r.id} className="flex items-center justify-between gap-3 py-3">
          <div className="min-w-0">
            <div className="truncate font-medium">
              {r.from_name || r.from_email} — {r.subject || "(no subject)"}
            </div>
            <div className="text-[12.5px] text-muted">
              {fmtTime(r.updated_at)} · {r.from_email}
              {r.proposal_kind ? ` · ${r.proposal_kind.replace(/_/g, " ")}` : ""}
              {r.thread_id ? (
                <>
                  {" · "}
                  <Link href={`/app/threads/${encodeURIComponent(r.thread_id)}`} className="text-accent hover:underline">
                    {r.thread_id}
                  </Link>
                </>
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusPill status={r.status} />
            {r.status === "paused" && (
              <Link href={`/app/approve${r.proposal_id ? `#${r.proposal_id}` : ""}`}>
                <Button className="py-1">Decide</Button>
              </Link>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

export default async function TodayPage() {
  const user = await requireProducer();
  const [inbox, outbox] = await Promise.all([
    tryFetch<{ items: InboxRow[] }>("/inbox?limit=200", user.name), // one read; the page partitions by status
    tryFetch<{ items: OutboxRow[] }>("/outbox?limit=20", user.name),
  ]);
  const error = inbox.error ?? outbox.error;
  if (error) return <ErrorState>Could not reach the agent service: {error}</ErrorState>;

  const rows = inbox.data?.items ?? [];
  const waiting = rows.filter((r) => r.status === "paused");
  const inFlight = rows.filter((r) => r.status === "running" || r.status === "new");
  const broken = rows.filter((r) => r.status === "failed");
  const sent = (outbox.data?.items ?? []).filter((o) => isToday(o.sent_at));
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });

  return (
    <>
      <PageHeader
        title={`Good day, ${user.displayName}`}
        subtitle={`${today} · ${waiting.length} waiting for you · ${inFlight.length} in flight · ${broken.length} need a look`}
        action={
          waiting.length > 0 ? (
            <Link href="/app/approve">
              <Button>Review {waiting.length} waiting</Button>
            </Link>
          ) : undefined
        }
      />
      <SectionTitle>Needs you</SectionTitle>
      <InboxList rows={waiting} empty="Nothing waiting. The crew has no proposal for you right now." />

      <SectionTitle>In flight</SectionTitle>
      <InboxList rows={inFlight} empty="No emails being worked on." />

      {broken.length > 0 && (
        <>
          <SectionTitle>Failed</SectionTitle>
          <p className="mb-2 text-[12.5px] text-muted">
            These turns failed after the worker's retries. Open the thread to see the audit trail and continue the run.
          </p>
          <InboxList rows={broken} empty="" />
        </>
      )}

      <SectionTitle>Sent today</SectionTitle>
      {sent.length === 0 ? (
        <EmptyState>No approved replies have gone out today.</EmptyState>
      ) : (
        <div className="card p-0">
          <table className="w-full text-[13px]">
            <thead className="text-left text-[11.5px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">Sent</th>
                <th className="px-3 py-2">To</th>
                <th className="px-3 py-2">Subject</th>
                <th className="px-3 py-2">Thread</th>
              </tr>
            </thead>
            <tbody>
              {sent.map((o) => (
                <tr key={o.id} className="border-t border-line">
                  <td className="px-3 py-2 whitespace-nowrap">{fmtTime(o.sent_at)}</td>
                  <td className="px-3 py-2">{o.to_email}</td>
                  <td className="px-3 py-2">{o.subject || "—"}</td>
                  <td className="px-3 py-2">
                    <Link href={`/app/threads/${encodeURIComponent(o.thread_id)}`} className="text-accent hover:underline">
                      {o.thread_id}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="disclaimer mt-6 text-center">
        Drafts are written by software and sent only after a licensed producer approves them. Producery never quotes,
        rates, binds, or gives coverage advice.
      </p>
    </>
  );
}
