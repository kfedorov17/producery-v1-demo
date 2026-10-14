"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "./ui";

/** Crash recovery for a thread cut off mid-node: POST /threads/{id}/continue (no new input). */
export function ContinueButton({ threadId }: { threadId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setErr(null);
    const res = await fetch(`/api/ui/threads/${encodeURIComponent(threadId)}/continue`, { method: "POST" });
    setBusy(false);
    if (!res.ok) {
      const d = (await res.json().catch(() => ({}))) as { detail?: string };
      setErr(d.detail ?? `failed (${res.status})`);
      return;
    }
    router.refresh();
  }
  return (
    <span className="flex items-center gap-2">
      <Button variant="ghost" disabled={busy} onClick={() => void go()}>
        {busy ? "Continuing…" : "Continue run"}
      </Button>
      {err && <span className="text-[12px] text-danger-fg">{err}</span>}
    </span>
  );
}
