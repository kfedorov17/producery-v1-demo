"use client";
/** Thumbs up/down + optional comment on a thread, saved as LangSmith feedback on its trace. */
import { useState } from "react";
import { cx } from "@/components/ui";

function Thumb({ down = false }: { down?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden style={down ? { transform: "scaleY(-1)" } : undefined}>
      <path d="M7 10v12" />
      <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
    </svg>
  );
}

export function FeedbackBar({ threadId, traceId }: { threadId: string; traceId?: string | null }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "composing" | "sent" | "error">("idle");

  async function send() {
    if (!rating) return;
    try {
      const res = await fetch(`/api/ui/threads/${encodeURIComponent(threadId)}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "user_rating", score: rating === "up" ? 1 : 0, comment: comment.slice(0, 500), trace_id: traceId ?? null }),
      });
      setState(res.ok ? "sent" : "error");
    } catch {
      setState("error");
    }
  }

  if (state === "sent") return <div className="mt-1.5 text-[11.5px] text-muted" role="status">Feedback recorded on the trace — thank you.</div>;
  return (
    <div className="mt-1.5">
      <div className="flex items-center gap-0.5">
        <button aria-label="Good outcome" aria-pressed={rating === "up"} onClick={() => { setRating("up"); setState("composing"); }} className={cx("rounded-btn p-1", rating === "up" ? "bg-success-bg text-success-fg" : "text-muted/70 hover:bg-hoverbg hover:text-ink")}>
          <Thumb />
        </button>
        <button aria-label="Bad outcome" aria-pressed={rating === "down"} onClick={() => { setRating("down"); setState("composing"); }} className={cx("rounded-btn p-1", rating === "down" ? "bg-danger-bg text-danger-fg" : "text-muted/70 hover:bg-hoverbg hover:text-ink")}>
          <Thumb down />
        </button>
        {state === "error" && <span className="ml-1 text-[11.5px] text-danger-fg">Could not save — try again</span>}
      </div>
      {state === "composing" && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <input aria-label="Feedback comment" placeholder="Add a comment (optional)…" value={comment} autoFocus onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void send(); }} className="input min-w-0 flex-1 py-1 text-[12.5px]" />
          <button onClick={() => void send()} className="bg-grad-bar shrink-0 rounded-btn px-2.5 py-1 text-[12px] font-medium text-white">Send</button>
        </div>
      )}
    </div>
  );
}
