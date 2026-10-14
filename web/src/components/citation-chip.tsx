import Link from "next/link";
import { parseCitation } from "@/lib/citations";
import { Chip } from "./ui";

export function CitationChip({ citation }: { citation: string }) {
  const c = parseCitation(citation);
  const chip = <Chip accent={c.kind !== "text"} title={citation}>{c.label}</Chip>;
  return c.href ? (
    <Link href={c.href} className="hover:brightness-95">
      {chip}
    </Link>
  ) : (
    chip
  );
}

export function CitationChips({ citations }: { citations: string[] | null | undefined }) {
  if (!citations || citations.length === 0) return <span className="text-[12.5px] text-muted">no citations</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {citations.map((c, i) => (
        <CitationChip key={`${c}-${i}`} citation={c} />
      ))}
    </div>
  );
}
