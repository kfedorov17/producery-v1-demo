/** Citation → chip convention for this solution. Two shapes exist:
 *  `[doc:doc-keystone-agreement#7]` (a document chunk the analyst retrieved) and `v_renewals.premium · HO-1001`
 *  (a column of an allowlisted view, optionally with the row id). Doc chips open Documents with the id pre-filled;
 *  view chips open Clients on that view with the id as the search. Anything else stays plain text. */

export interface ParsedCitation {
  label: string;
  kind: "doc" | "view" | "text";
  href: string | null;
}

const DOC = /\[?doc:(doc-[\w-]+)(?:#(\d+))?\]?/i;
const VIEW = /^(v_[a-z_]+)(?:\.([a-z_]+))?(?:\s*·\s*(.+))?$/i;

export function parseCitation(raw: string): ParsedCitation {
  const c = String(raw).trim();
  const d = c.match(DOC);
  if (d) {
    const id = d[1]!;
    const chunk = d[2] ? `#${d[2]}` : "";
    return { label: `${id}${chunk}`, kind: "doc", href: `/app/documents?q=${encodeURIComponent(id)}` };
  }
  const v = c.match(VIEW);
  if (v) {
    const view = v[1]!.toLowerCase();
    const col = v[2] ? `.${v[2]}` : "";
    const id = v[3]?.trim();
    const q = id ? `&q=${encodeURIComponent(id)}` : "";
    return { label: `${view}${col}${id ? ` · ${id}` : ""}`, kind: "view", href: `/app/clients?view=${view}${q}` };
  }
  return { label: c, kind: "text", href: null };
}
