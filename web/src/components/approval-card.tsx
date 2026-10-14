"use client";
/** Decision cards approve / edit & approve /
 *  reject-with-reason, bulk select, draft preview, the owner brief with citation chips and the compliance flags.
 *  Each decision goes to POST /api/ui/threads/{thread}/resume; the proxy stamps `by` with the signed-in producer.
 *  After an edit the card shows the flags the server raised on the edited text — the producer sees what they overrode. */
import { useState } from "react";
import Link from "next/link";
import { Button, Chip, StatusPill, cx } from "./ui";
import { CitationChips } from "./citation-chip";

export interface ComplianceFlag {
  code: string;
  why?: string;
  excerpt?: string;
}

export interface ProposalItem {
  id: string;
  thread_id: string;
  kind: "send_email" | "create_task" | "update_record";
  payload: Record<string, unknown>;
  owner_brief: string | null;
  compliance_flags: ComplianceFlag[];
  status: string;
  email_id?: string | null;
  from_email?: string | null;
  from_name?: string | null;
  subject?: string | null;
  received_at?: string | null;
}

interface DecisionResult {
  values?: { outcome?: string; compliance_flags?: ComplianceFlag[] };
  trace_id?: string;
  status?: string;
  detail?: string;
}

async function decide(threadId: string, body: Record<string, unknown>): Promise<DecisionResult> {
  const res = await fetch(`/api/ui/threads/${encodeURIComponent(threadId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as DecisionResult;
  if (res.status === 409) throw new Error("already decided");
  if (!res.ok) throw new Error(data.detail ?? `failed (${res.status})`);
  window.dispatchEvent(new CustomEvent("producery:approvals-changed"));
  return data;
}

/** Citations the drafter attached ride in the owner brief's parent state; the proposal row carries the brief only,
 *  so we pull `view.column · id` and `[doc:…]` markers out of the brief text for the chips. */
export function citationsFromBrief(brief: string | null | undefined): string[] {
  if (!brief) return [];
  const out = new Set<string>();
  for (const m of brief.matchAll(/\[doc:[\w#-]+\]/g)) out.add(m[0]);
  for (const m of brief.matchAll(/\bv_[a-z_]+(?:\.[a-z_]+)?(?:\s*·\s*[A-Z]{2,3}-[\w-]+)?/g)) out.add(m[0].trim());
  return [...out];
}

function kindLabel(k: ProposalItem["kind"]): string {
  return { send_email: "reply to client", create_task: "task for you", update_record: "record update" }[k];
}

export function ApprovalCard({
  item,
  selected,
  onSelect,
  traceBase = null,
}: {
  item: ProposalItem;
  selected: boolean;
  onSelect: (id: string, on: boolean) => void;
  traceBase?: string | null;
}) {
  const p = item.payload as { to?: string; subject?: string; body?: string; title?: string; detail?: string; fields?: Record<string, unknown>; verify_fields?: string[]; doc_type?: string };
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [subject, setSubject] = useState(p.subject ?? "");
  const [text, setText] = useState(p.body ?? "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ label: string; status: string; flags: ComplianceFlag[]; trace: string | null } | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(body: Record<string, unknown>, label: string, status: string) {
    setError(null);
    setBusy(true);
    try {
      const out = await decide(item.thread_id, body);
      setDone({ label, status, flags: out.values?.compliance_flags ?? [], trace: out.trace_id ?? null });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const summary = item.subject ? `${item.from_name || item.from_email || "?"} — ${item.subject}` : item.thread_id;
  const citations = citationsFromBrief(item.owner_brief);

  if (done) {
    return (
      <div className="card flex flex-col gap-2 p-3 opacity-80" data-testid="decided-card">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm">{summary}</span>
          <StatusPill status={done.status} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
          <span>{done.label}</span>
          {done.flags.length > 0 && (
            <span className="text-danger-fg">
              flagged after sending: {done.flags.map((f) => f.code).join(", ")}
            </span>
          )}
          <Link href={`/app/threads/${encodeURIComponent(item.thread_id)}`} className="text-accent hover:underline">
            thread →
          </Link>
          {done.trace &&
            (traceBase ? (
              <a href={`${traceBase}${done.trace}?poll=true`} target="_blank" rel="noreferrer" className="font-mono text-[11px] text-accent hover:underline">
                trace {done.trace.slice(0, 8)}… ↗
              </a>
            ) : (
              <span className="font-mono text-[11px]">trace {done.trace.slice(0, 8)}…</span>
            ))}
        </div>
      </div>
    );
  }

  return (
    <div id={item.id} className={cx("card", busy && "opacity-60")} data-testid="approval-card">
      <div className="flex items-start justify-between gap-3">
        <label className="flex min-w-0 items-start gap-2.5">
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onSelect(item.id, e.target.checked)}
            className="mt-1 h-4 w-4 accent-[#1ab394]"
            aria-label="Select for bulk approve"
          />
          <span className="min-w-0">
            <span className="block truncate font-medium">{summary}</span>
            <span className="mt-0.5 block text-[12.5px] text-muted">
              {kindLabel(item.kind)} · <Link href={`/app/threads/${encodeURIComponent(item.thread_id)}`} className="text-accent hover:underline">{item.thread_id}</Link>
            </span>
          </span>
        </label>
        <div className="flex shrink-0 items-center gap-1.5">
          <Chip accent={item.kind === "send_email"}>{item.kind.replace(/_/g, " ")}</Chip>
          <StatusPill status="pending" />
        </div>
      </div>

      <div className="mt-3 rounded-btn border border-line p-2.5">
        <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted">Owner brief (for you, not the client)</div>
        <div className="whitespace-pre-wrap text-[13px] leading-relaxed">{item.owner_brief || "—"}</div>
        <div className="mt-2">
          <CitationChips citations={citations} />
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12.5px]">
        <span className="text-muted">Compliance gate:</span>
        {item.compliance_flags.length === 0 ? (
          <Chip accent>no flags</Chip>
        ) : (
          item.compliance_flags.map((f, i) => (
            <span key={i} className="rounded-chip bg-danger-bg px-2 py-0.5 text-danger-fg" title={f.why}>
              {f.code}
              {f.excerpt ? ` — "${f.excerpt}"` : ""}
            </span>
          ))
        )}
      </div>

      {item.kind === "send_email" && mode === "view" && (
        <div className="mt-2 rounded-btn bg-chip p-2.5 text-[13px] leading-relaxed" data-testid="draft">
          <div className="mb-1 text-[12px] text-muted">To: {p.to}{p.subject ? ` · ${p.subject}` : ""}</div>
          <div className="whitespace-pre-wrap">{p.body || "(no draft body — the drafter refused; a review task is proposed instead)"}</div>
        </div>
      )}
      {item.kind === "create_task" && mode === "view" && (
        <div className="mt-2 rounded-btn bg-chip p-2.5 text-[13px]">
          <div className="font-medium">{p.title}</div>
          <div className="text-muted">{p.detail}</div>
        </div>
      )}
      {item.kind === "update_record" && mode === "view" && (
        <div className="mt-2 rounded-btn bg-chip p-2.5 text-[13px]">
          <div className="mb-1 text-muted">{p.doc_type} · fields extracted</div>
          <table className="w-full text-[12.5px]">
            <tbody>
              {Object.entries(p.fields ?? {}).map(([k, v]) => (
                <tr key={k} className={cx((p.verify_fields ?? []).includes(k) && "text-warning-fg")}>
                  <td className="pr-3 font-mono">{k}</td>
                  <td>{String(v ?? "—")}{(p.verify_fields ?? []).includes(k) ? " · verify" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {mode === "edit" && (
        <div className="mt-2 flex flex-col gap-2">
          <input value={subject} onChange={(e) => setSubject(e.target.value)} className="input w-full" aria-label="Edit subject" />
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} className="input w-full" aria-label="Edit draft" />
        </div>
      )}
      {mode === "reject" && (
        <div className="mt-2">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why? (required — this trains the drafts)"
            className="input w-full"
            aria-label="Rejection reason"
          />
        </div>
      )}

      {error && <div className="mt-2 rounded-btn bg-danger-bg px-3 py-2 text-[13px] text-danger-fg" role="alert">{error}</div>}

      <div className="mt-3 flex flex-wrap gap-2">
        {mode === "view" && (
          <>
            <Button disabled={busy} onClick={() => void act({ action: "approve" }, "approved", "approved")}>Approve</Button>
            {item.kind === "send_email" && (
              <Button variant="ghost" disabled={busy} onClick={() => setMode("edit")}>Edit &amp; approve</Button>
            )}
            <Button variant="danger" disabled={busy} onClick={() => setMode("reject")}>Reject</Button>
          </>
        )}
        {mode === "edit" && (
          <>
            <Button
              disabled={busy || !text.trim()}
              onClick={() => void act({ action: "edit", payload: { to: p.to, subject, body: text } }, "sent with your edits", "edited")}
            >
              Send edited
            </Button>
            <Button variant="ghost" onClick={() => setMode("view")}>Cancel</Button>
          </>
        )}
        {mode === "reject" && (
          <>
            <Button variant="danger" disabled={busy || !reason.trim()} onClick={() => void act({ action: "reject", reason }, `rejected — ${reason}`, "rejected")}>
              Confirm reject
            </Button>
            <Button variant="ghost" onClick={() => setMode("view")}>Cancel</Button>
          </>
        )}
      </div>
    </div>
  );
}

export function BulkBar({ items, selectedIds, onDone }: { items: ProposalItem[]; selectedIds: string[]; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const tasks = items.filter((i) => selectedIds.includes(i.id) && i.kind === "create_task");
  if (selectedIds.length === 0 && !note) return null;

  async function approveAll() {
    setBusy(true);
    let ok = 0;
    let failed = 0;
    for (const t of tasks) {
      try {
        await decide(t.thread_id, { action: "approve" });
        ok++;
      } catch {
        failed++;
      }
    }
    setNote(`${ok} task${ok === 1 ? "" : "s"} approved${failed ? `, ${failed} failed` : ""}${selectedIds.length > tasks.length ? " — replies and record updates need a one-by-one look" : ""}`);
    setBusy(false);
    onDone();
    window.setTimeout(() => window.location.reload(), 800);
  }

  return (
    <div className="sticky top-14 z-10 mb-3 flex items-center gap-3 rounded-btn border border-line bg-surface px-3 py-2 shadow-cardhover">
      {selectedIds.length > 0 && (
        <>
          <span className="text-sm font-medium">{selectedIds.length} selected · {tasks.length} task{tasks.length === 1 ? "" : "s"}</span>
          <Button onClick={() => void approveAll()} disabled={busy || tasks.length === 0}>
            {busy ? "Approving…" : "Approve selected tasks"}
          </Button>
        </>
      )}
      {note && <span className="text-[13px] text-muted">{note}</span>}
    </div>
  );
}
