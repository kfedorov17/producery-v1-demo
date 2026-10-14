/** Documents — what the analyst can cite: the ingested library and a search box that runs the same
 *  `documents_search` tool the agent uses (design.md §14). Upload lives on Connections. */
import Link from "next/link";
import { Chip, EmptyState, ErrorState, PageHeader, SectionTitle, StatusPill } from "@/components/ui";
import { tryFetch } from "@/lib/api";
import { fmtTime } from "@/lib/dates";
import { requireProducer } from "@/lib/session";

interface DocRow {
  id: string;
  title: string;
  doc_type: string;
  status: string;
  chunk_count: number;
  created_at: string;
  ingested_at: string | null;
}
/** Shape of a `documents_search` hit (producery/mcp_data/server.py): id = `<doc id>#<chunk>`, section, snippet, citation. */
interface Hit {
  id?: string;
  document_id?: string;
  title?: string;
  doc_type?: string;
  section?: string;
  snippet?: string;
  score?: number;
  citation?: string;
}
interface SearchPayload {
  hits?: Hit[];
  error?: { code: string; message: string };
}

export default async function DocumentsPage({ searchParams }: { searchParams: Promise<{ q?: string }> }) {
  const user = await requireProducer();
  const q = ((await searchParams).q ?? "").trim();
  const [docs, search] = await Promise.all([
    tryFetch<{ items: DocRow[] }>("/documents", user.name),
    q ? tryFetch<SearchPayload>(`/documents/search?q=${encodeURIComponent(q)}&k=5`, user.name) : Promise.resolve({ data: null, error: null }),
  ]);
  const rows = docs.data?.items ?? [];
  const hits = search.data?.hits ?? [];
  const matchingDoc = q ? rows.filter((d) => d.id.includes(q) || d.title.toLowerCase().includes(q.toLowerCase())) : [];

  return (
    <>
      <PageHeader title="Documents" subtitle="Every document the crew can cite. Search runs the analyst's own retrieval tool." />
      <form className="mb-4 flex gap-2" action="/app/documents" method="get">
        <input name="q" defaultValue={q} placeholder="Search the library (e.g. roof age, flood exclusions)…" className="input flex-1" aria-label="Search documents" />
        <button className="bg-grad-bar rounded-btn px-3 py-2 text-sm font-medium text-white">Search</button>
      </form>

      {q && (
        <>
          <SectionTitle>Results for “{q}”</SectionTitle>
          {search.error || search.data?.error ? (
            <ErrorState>{search.error ?? search.data?.error?.message}</ErrorState>
          ) : hits.length === 0 && matchingDoc.length === 0 ? (
            <EmptyState>No chunks matched.</EmptyState>
          ) : (
            <div className="flex flex-col gap-2">
              {matchingDoc.map((d) => (
                <div key={d.id} className="card py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{d.title}</span>
                    <Chip accent>{d.id}</Chip>
                  </div>
                  <div className="text-[12.5px] text-muted">document · {d.doc_type} · {d.chunk_count} chunks</div>
                </div>
              ))}
              {hits.map((h, i) => {
                const id = h.id ?? h.document_id ?? "";
                return (
                  <div key={i} className="card py-3" data-testid="search-hit">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">
                        {h.title ?? id}
                        {h.section ? <span className="text-muted"> · {h.section}</span> : null}
                      </span>
                      <Chip accent>{h.citation ?? `[doc:${id}]`}</Chip>
                    </div>
                    <div className="mt-1 whitespace-pre-wrap text-[13px] text-ink/90">{(h.snippet ?? "").slice(0, 600)}</div>
                    {typeof h.score === "number" && <div className="mt-1 text-[11.5px] text-muted">score {h.score.toFixed(3)}</div>}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      <SectionTitle>Library</SectionTitle>
      {docs.error ? (
        <ErrorState>{docs.error}</ErrorState>
      ) : rows.length === 0 ? (
        <EmptyState>
          No documents yet. <Link href="/app/connections" className="text-accent hover:underline">Upload one on Connections</Link>.
        </EmptyState>
      ) : (
        <div className="card p-0">
          <table className="w-full text-[13px]">
            <thead className="text-left text-[11.5px] uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Id</th>
                <th className="px-3 py-2">Type</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Chunks</th>
                <th className="px-3 py-2">Ingested</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className="border-t border-line" data-testid="doc-row">
                  <td className="px-3 py-2 font-medium">{d.title}</td>
                  <td className="px-3 py-2 font-mono text-[12px]">{d.id}</td>
                  <td className="px-3 py-2">{d.doc_type}</td>
                  <td className="px-3 py-2"><StatusPill status={d.status} /></td>
                  <td className="px-3 py-2">{d.chunk_count}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{fmtTime(d.ingested_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[12.5px] text-muted">
        Add documents on <Link href="/app/connections" className="text-accent hover:underline">Connections</Link> (OneDrive / Google Drive manual mode).
      </p>
    </>
  );
}
