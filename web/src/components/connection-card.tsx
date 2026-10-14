"use client";
/** Connections in manual mode (design.md §14): Email (paste an inbound email), OneDrive and Google Drive
 *  (upload a document). Each card keeps the connector's setup checklist as manual tasks the producer ticks off; the
 *  state persists through PUT /connections/{provider}. Nothing here calls Microsoft or Google. */
import { useState } from "react";
import Link from "next/link";
import { Button, Chip, StatusPill } from "./ui";

export interface ConnectionRow {
  provider: "email" | "onedrive" | "gdrive";
  status: "manual" | "connecting" | "connected";
  steps_done: string[];
  updated_at: string | null;
}

const META: Record<ConnectionRow["provider"], { title: string; blurb: string; steps: { id: string; label: string }[] }> = {
  email: {
    title: "Email",
    blurb: "Inbound mail becomes proposals you approve. Manual mode: paste an email below. Automated capture is a later connector.",
    steps: [
      { id: "forward-rule", label: "Create a forwarding rule from the agency mailbox to the Producery capture address" },
      { id: "test-forward", label: "Forward one real email and confirm it appears on Today" },
      { id: "signature", label: "Set the producer signature the platform appends to approved replies" },
    ],
  },
  onedrive: {
    title: "OneDrive / SharePoint",
    blurb: "Synced documents become searchable, cited sources. Manual mode: upload a document below (md or txt).",
    steps: [
      { id: "shared-folder", label: "Create the shared folder 'Producery' and move dec pages and agreements into it" },
      { id: "app-registration", label: "IT: Azure app registration 'Producery Docsync' with Files.Read.All and admin consent" },
      { id: "secret-expiry", label: "IT: record the client secret expiry date" },
    ],
  },
  gdrive: {
    title: "Google Drive",
    blurb: "Synced documents become searchable, cited sources. Manual mode: upload a document below (md or txt).",
    steps: [
      { id: "shared-folder", label: "Create the shared folder 'Producery' in the agency's Drive" },
      { id: "cloud-project", label: "IT: Google Cloud project with the Drive API enabled and a drive.readonly OAuth client" },
      { id: "consent", label: "IT: consent screen (internal for Workspace) and the redirect URI" },
    ],
  },
};

async function putConnection(provider: string, steps_done: string[]) {
  const res = await fetch(`/api/ui/connections/${provider}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "manual", steps_done }) });
  if (!res.ok) throw new Error(`save failed (${res.status})`);
}

export function ConnectionCard({ row }: { row: ConnectionRow }) {
  const meta = META[row.provider];
  const [done, setDone] = useState<string[]>(row.steps_done ?? []);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(id: string, on: boolean) {
    const next = on ? [...new Set([...done, id])] : done.filter((s) => s !== id);
    setDone(next);
    try {
      await putConnection(row.provider, next);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="card" data-testid={`connection-${row.provider}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="font-semibold text-heading-accent">{meta.title}</div>
        <div className="flex items-center gap-1.5">
          <Chip>{done.length}/{meta.steps.length} steps</Chip>
          <StatusPill status={row.status} />
        </div>
      </div>
      <p className="mt-1 text-[12.5px] text-muted">{meta.blurb}</p>

      <div className="mt-3 border-t border-line pt-2">
        <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted">Manual mode</div>
        {row.provider === "email" ? <PasteEmail onDone={setNote} /> : <UploadDocument provider={row.provider} onDone={setNote} />}
        {note && <div className="mt-2 text-[12.5px] text-success-fg" role="status">{note}</div>}
      </div>

      <div className="mt-3 border-t border-line pt-2">
        <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted">Setup checklist (manual tasks)</div>
        <ul className="flex flex-col gap-1.5">
          {meta.steps.map((s) => (
            <li key={s.id}>
              <label className="flex items-start gap-2 text-[13px]">
                <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[#1ab394]" checked={done.includes(s.id)} onChange={(e) => void toggle(s.id, e.target.checked)} aria-label={s.label} />
                <span className={done.includes(s.id) ? "text-muted line-through" : ""}>{s.label}</span>
              </label>
            </li>
          ))}
        </ul>
        {error && <div className="mt-2 text-[12.5px] text-danger-fg">{error}</div>}
      </div>
    </div>
  );
}

function PasteEmail({ onDone }: { onDone: (s: string) => void }) {
  const [from, setFrom] = useState("");
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [thread, setThread] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch("/api/ui/inbox", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ from_email: from, from_name: name, subject, body }) });
      const data = (await res.json()) as { thread_id?: string; detail?: unknown };
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `failed (${res.status})`);
      setThread(data.thread_id ?? null);
      onDone(`Captured as ${data.thread_id}. The worker picks it up within 15 seconds; watch Today.`);
      setBody("");
      setSubject("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <input className="input" placeholder="From email" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="From email" />
        <input className="input" placeholder="From name" value={name} onChange={(e) => setName(e.target.value)} aria-label="From name" />
      </div>
      <input className="input" placeholder="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Subject" />
      <textarea className="input" rows={4} placeholder="Paste the email body (treated as data, never as instructions)" value={body} onChange={(e) => setBody(e.target.value)} aria-label="Email body" />
      <div className="flex items-center gap-2">
        <Button disabled={busy || !from || !body.trim()} onClick={() => void submit()}>{busy ? "Capturing…" : "Capture email"}</Button>
        {thread && <Link href="/app" className="text-[12.5px] text-accent hover:underline">go to Today →</Link>}
      </div>
      {err && <div className="text-[12.5px] text-danger-fg" role="alert">{err}</div>}
    </div>
  );
}

function UploadDocument({ provider, onDone }: { provider: "onedrive" | "gdrive"; onDone: (s: string) => void }) {
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState<"contract" | "dec_page" | "help" | "other">("other");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      if (!/\.(md|txt|markdown)$/i.test(file.name)) throw new Error("manual mode accepts .md or .txt in this build");
      const text = await file.text();
      const res = await fetch("/api/ui/documents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: title || file.name, doc_type: docType, text, provider }) });
      const data = (await res.json()) as { id?: string; queued?: boolean; detail?: unknown };
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `failed (${res.status})`);
      onDone(data.queued ? `Stored as ${data.id} and queued for ingestion — see Documents.` : `${data.id} is already ingested (unchanged content).`);
      setFile(null);
      setTitle("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <input className="input" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Document title" />
        <select className="input" value={docType} onChange={(e) => setDocType(e.target.value as typeof docType)} aria-label="Document type">
          <option value="dec_page">declarations page</option>
          <option value="contract">agreement / contract</option>
          <option value="help">help article</option>
          <option value="other">other</option>
        </select>
      </div>
      <input type="file" accept=".md,.txt,.markdown" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Document file" className="text-[12.5px]" />
      <div>
        <Button disabled={busy || !file} onClick={() => void submit()}>{busy ? "Uploading…" : "Upload document"}</Button>
      </div>
      {err && <div className="text-[12.5px] text-danger-fg" role="alert">{err}</div>}
    </div>
  );
}
