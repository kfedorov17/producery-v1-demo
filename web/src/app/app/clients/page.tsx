/** Clients — the book of business through the allowlisted read-only views, the exact surface the analyst sees
 *  (design.md §14). No raw tables, no writes. */
import Link from "next/link";
import { EmptyState, ErrorState, PageHeader, cx } from "@/components/ui";
import { tryFetch } from "@/lib/api";
import { fmtDate } from "@/lib/dates";
import { requireProducer } from "@/lib/session";

const VIEWS = [
  { id: "v_households", label: "Households" },
  { id: "v_policies", label: "Policies" },
  { id: "v_renewals", label: "Renewals" },
  { id: "v_appointments", label: "Appointments" },
];

interface BookPayload {
  view: string;
  items: Record<string, unknown>[];
  total: number;
  truncated: boolean;
}

function cell(k: string, v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (/_date$|_at$|^expires|^effective|^renewal/.test(k) && typeof v === "string") return fmtDate(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default async function ClientsPage({ searchParams }: { searchParams: Promise<{ view?: string; q?: string }> }) {
  const user = await requireProducer();
  const sp = await searchParams;
  const view = VIEWS.some((v) => v.id === sp.view) ? sp.view! : "v_households";
  const q = (sp.q ?? "").trim();
  const { data, error } = await tryFetch<BookPayload>(`/book/${view}${q ? `?q=${encodeURIComponent(q)}` : ""}`, user.name);
  const rows = data?.items ?? [];
  const cols = rows.length > 0 ? Object.keys(rows[0]!) : [];

  return (
    <>
      <PageHeader title="Clients" subtitle="Your book, through the same read-only views the analyst is allowed to query." />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <nav className="flex gap-1 rounded-pill bg-chip p-1" aria-label="Views">
          {VIEWS.map((v) => (
            <Link
              key={v.id}
              href={`/app/clients?view=${v.id}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
              className={cx("rounded-pill px-3.5 py-1.5 text-[13px] font-semibold", v.id === view ? "bg-grad-bar text-white shadow-sm" : "text-muted hover:text-ink")}
              aria-current={v.id === view ? "page" : undefined}
            >
              {v.label}
            </Link>
          ))}
        </nav>
        <form className="ml-auto flex gap-2" action="/app/clients" method="get">
          <input type="hidden" name="view" value={view} />
          <input name="q" defaultValue={q} placeholder="Search by name or id…" className="input w-64" aria-label="Search" />
          <button className="bg-grad-bar rounded-btn px-3 py-2 text-sm font-medium text-white">Search</button>
        </form>
      </div>
      {error ? (
        <ErrorState>Could not query {view}: {error}</ErrorState>
      ) : rows.length === 0 ? (
        <EmptyState>No rows{q ? ` match "${q}"` : ""} in {view}.</EmptyState>
      ) : (
        <div className="card overflow-x-auto p-0" data-testid="book-table">
          <table className="w-full text-[13px]">
            <thead className="text-left text-[11.5px] uppercase tracking-wide text-muted">
              <tr>
                {cols.map((c) => (
                  <th key={c} className="px-3 py-2 whitespace-nowrap">{c.replace(/_/g, " ")}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-line hover:bg-hoverbg">
                  {cols.map((c) => (
                    <td key={c} className="px-3 py-2 whitespace-nowrap">{cell(c, r[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-line px-3 py-2 text-[12px] text-muted">
            {data?.total} row{data?.total === 1 ? "" : "s"}
            {data?.truncated ? " · more exist — narrow the search" : ""} · source view {view}
          </div>
        </div>
      )}
    </>
  );
}
