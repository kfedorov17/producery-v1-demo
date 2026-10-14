/** Connections — Email, OneDrive, Google Drive in manual mode with their setup checklists (design.md §14). */
import { ConnectionCard, type ConnectionRow } from "@/components/connection-card";
import { ErrorState, PageHeader } from "@/components/ui";
import { tryFetch } from "@/lib/api";
import { requireProducer } from "@/lib/session";

export default async function ConnectionsPage() {
  const user = await requireProducer();
  const { data, error } = await tryFetch<{ items: ConnectionRow[] }>("/connections", user.name);
  if (error) return <ErrorState>Could not load connections: {error}</ErrorState>;
  const order: ConnectionRow["provider"][] = ["email", "onedrive", "gdrive"];
  const rows = order.map((p) => data?.items.find((r) => r.provider === p) ?? { provider: p, status: "manual" as const, steps_done: [], updated_at: null });
  return (
    <>
      <PageHeader
        title="Connections"
        subtitle="How work reaches the crew. Phase 1 is manual: paste emails and upload documents here. Automatic sync is a later connector."
      />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {rows.map((r) => (
          <ConnectionCard key={r.provider} row={r} />
        ))}
      </div>
    </>
  );
}
