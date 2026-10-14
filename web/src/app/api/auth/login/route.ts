import { NextResponse } from "next/server";
import { demoAccountBlocked, verifyProducer } from "@/lib/producers";
import { getSession } from "@/lib/session";

/** Tiny in-memory throttle: 10 failed attempts per name per minute. Enough for a single-instance producer UI. */
const failures = new Map<string, number[]>();
function throttled(name: string): boolean {
  const now = Date.now();
  for (const [n, ts] of failures) if (!ts.some((t) => now - t < 60_000)) failures.delete(n); // bounded: quiet names are forgotten
  const recent = (failures.get(name) ?? []).filter((t) => now - t < 60_000);
  if (recent.length) failures.set(name, recent);
  else failures.delete(name);
  return recent.length >= 10;
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { name?: string; password?: string };
  const name = String(body.name ?? "").trim().toLowerCase();
  const password = String(body.password ?? "");
  if (!name || !password) return NextResponse.json({ detail: "name and password required" }, { status: 400 });
  if (throttled(name)) return NextResponse.json({ detail: "too many attempts" }, { status: 429 });
  const blocked = demoAccountBlocked();
  if (blocked) return NextResponse.json({ detail: blocked }, { status: 503 });
  const account = await verifyProducer(name, password);
  if (!account) {
    failures.set(name, [...(failures.get(name) ?? []), Date.now()]);
    return NextResponse.json({ detail: "invalid credentials" }, { status: 401 });
  }
  const session = await getSession();
  session.name = account.name;
  session.displayName = account.display_name || account.name;
  session.role = "producer";
  session.signedInAt = new Date().toISOString();
  await session.save();
  return NextResponse.json({ ok: true, name: account.name });
}
