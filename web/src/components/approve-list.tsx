"use client";
/** Client list wrapper: bulk selection state over ApprovalCards. */
import { useState } from "react";
import { ApprovalCard, BulkBar, type ProposalItem } from "./approval-card";

export function ApproveList({ items, traceBase }: { items: ProposalItem[]; traceBase: string | null }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const onSelect = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  return (
    <div className="flex flex-col gap-3">
      <BulkBar items={items} selectedIds={[...selected]} onDone={() => setSelected(new Set())} />
      <label className="flex items-center gap-2 px-1 text-[13px] text-muted">
        <input
          type="checkbox"
          checked={selected.size === items.length && items.length > 0}
          onChange={(e) => setSelected(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())}
          className="h-4 w-4 accent-[#1ab394]"
        />
        Select all ({items.length})
      </label>
      {items.map((item) => (
        <ApprovalCard key={item.id} item={item} selected={selected.has(item.id)} onSelect={onSelect} traceBase={traceBase} />
      ))}
    </div>
  );
}
