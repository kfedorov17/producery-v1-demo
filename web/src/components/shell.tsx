"use client";
/** App shell: a top bar with the brand, the pill nav, the "More" overflow
 *  menu with the signed-in producer, and four bottom tabs on phones. Producer scope: Today · Approve · Clients in
 *  the pill; Documents · Connections · Settings under More. No Chat, Work, Leads or Admin entries. */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Producer } from "@/lib/session";
import { ProduceryLogo, cx } from "./ui";

const DESTINATIONS = [
  { label: "Today", href: "/app", icon: "☀" },
  { label: "Approve", href: "/app/approve", icon: "✓" },
  { label: "Clients", href: "/app/clients", icon: "👥" },
];
const MORE = [
  { label: "Documents", href: "/app/documents" },
  { label: "Connections", href: "/app/connections" },
  { label: "Settings", href: "/app/settings" },
];

function activeHref(pathname: string): string {
  if (pathname === "/app") return "/app";
  for (const d of DESTINATIONS.slice(1)) if (pathname.startsWith(d.href)) return d.href;
  if (pathname.startsWith("/app/threads")) return "/app/approve";
  return "";
}

function usePendingCount(): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch("/api/ui/proposals?status=pending&limit=50")
        .then((r) => (r.ok ? r.json() : { total: 0 }))
        .then((d: { total?: number }) => {
          if (!cancelled) setCount(d.total ?? 0);
        })
        .catch(() => undefined);
    };
    poll();
    const t = window.setInterval(poll, 15_000);
    const onChanged = () => poll();
    window.addEventListener("producery:approvals-changed", onChanged);
    window.addEventListener("focus", onChanged);
    return () => {
      cancelled = true;
      window.clearInterval(t);
      window.removeEventListener("producery:approvals-changed", onChanged);
      window.removeEventListener("focus", onChanged);
    };
  }, []);
  return count;
}

function UtilityMenu({ user }: { user: Producer }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button
        aria-label="More"
        onClick={() => setOpen(!open)}
        className="flex h-9 w-9 items-center justify-center rounded-btn text-ink hover:bg-hoverbg"
      >
        ⋯
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-40 w-[200px] rounded-btn border border-line bg-surface py-1 shadow-popover">
          {MORE.map((i) => (
            <Link key={i.href} href={i.href} onClick={() => setOpen(false)} className="block px-3.5 py-2 text-[13px] text-ink hover:bg-hoverbg">
              {i.label}
            </Link>
          ))}
          <div className="mt-1 border-t border-line px-3.5 pb-1 pt-2 text-[12px] text-muted">
            {user.displayName}
            <div className="text-[10.5px] uppercase tracking-wide">{user.role}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export function AppShell({ user, children }: { user: Producer; children: ReactNode }) {
  const pathname = usePathname();
  const active = activeHref(pathname);
  const pending = usePendingCount();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-surface px-3 md:px-5">
        <Link href="/app" aria-label="Producery home" className="shrink-0">
          <ProduceryLogo size={19} />
        </Link>
        <nav className="hidden items-center gap-1 rounded-pill bg-chip p-1 md:flex" aria-label="Primary">
          {DESTINATIONS.map((d) => {
            const on = active === d.href;
            return (
              <Link
                key={d.href}
                href={d.href}
                aria-current={on ? "page" : undefined}
                className={cx(
                  "flex items-center gap-1.5 rounded-pill px-3.5 py-1.5 text-[13px] font-semibold transition-colors",
                  on ? "bg-grad-bar text-white shadow-sm" : "text-muted hover:text-ink",
                )}
              >
                {d.label}
                {d.href === "/app/approve" && pending > 0 && (
                  <span
                    aria-label={`${pending} waiting`}
                    className={cx("rounded-full px-1.5 text-[10.5px] font-bold", on ? "bg-white/25" : "bg-chip-accent text-heading-accent")}
                  >
                    {pending > 9 ? "9+" : pending} waiting
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto" />
        <UtilityMenu user={user} />
      </header>

      <main className="mx-auto w-full max-w-content flex-1 p-4 pb-24 md:p-6 md:pb-6">{children}</main>

      <nav className="fixed inset-x-0 bottom-0 z-10 flex justify-around border-t border-line bg-surface py-1.5 md:hidden">
        {[...DESTINATIONS, { label: "More", href: "/app/settings", icon: "⋯" }].map((n) => (
          <Link key={n.href} href={n.href} className="relative flex flex-col items-center px-2 py-1 text-[11px] text-ink">
            <span aria-hidden className="text-[16px]">{n.icon}</span>
            {n.label}
            {n.href === "/app/approve" && pending > 0 && (
              <span className="absolute right-0 top-0 rounded-full bg-[#dc2626] px-1 text-[9px] font-bold text-white">
                {pending > 9 ? "9+" : pending}
              </span>
            )}
          </Link>
        ))}
      </nav>
    </div>
  );
}
