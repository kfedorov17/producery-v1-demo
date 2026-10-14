/** Approve — every pending proposal as a decision card (design.md §14). */
import { ApproveList } from "@/components/approve-list";
import type { ProposalItem } from "@/components/approval-card";
import { EmptyState, ErrorState, PageHeader } from "@/components/ui";
import { traceBase, tryFetch } from "@/lib/api";
import { requireProducer } from "@/lib/session";

export default async function ApprovePage() {
  const user = await requireProducer();
  const { data, error } = await tryFetch<{ items: ProposalItem[]; total: number }>("/proposals?status=pending&limit=100", user.name);
  if (error) return <ErrorState>Could not load proposals: {error}</ErrorState>;
  const items = data?.items ?? [];
  return (
    <>
      <PageHeader
        title="Approve"
        subtitle={items.length === 0 ? "Nothing waiting for a decision." : `${items.length} proposal${items.length === 1 ? "" : "s"} waiting. Approve, edit, or reject — every decision is audited under your name.`}
      />
      {items.length === 0 ? <EmptyState>All clear. New proposals appear here as the crew finishes.</EmptyState> : <ApproveList items={items} traceBase={traceBase()} />}
    </>
  );
}
