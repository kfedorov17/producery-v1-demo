import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { NextResponse } from "next/server";
import { demoAccountBlocked } from "@/lib/producers";

/** Liveness for the compose healthcheck and `pipeline.py wait-ready`. Honest: without a usable SESSION_SECRET every
 *  page would 500 (the session cookie cannot be sealed), so readiness says so instead of reporting ok (hosted CI
 *  run 38035107210 found this: health was green while the whole UI was down). */
export function GET() {
  const secret = process.env.SESSION_SECRET ?? "";
  if (secret.length < 32) {
    return NextResponse.json({ ok: false, service: "web", detail: "SESSION_SECRET missing or shorter than 32 characters" }, { status: 503 });
  }
  if (!process.env.AGENT_API_TOKEN) {
    return NextResponse.json({ ok: false, service: "web", detail: "AGENT_API_TOKEN is not configured" }, { status: 503 });
  }
  const roster = process.env.PRODUCERS_FILE ?? resolve(process.cwd(), "..", "config", "producers.yaml");
  if (!existsSync(roster)) {
    return NextResponse.json({ ok: false, service: "web", detail: `producer roster not found at ${roster} (mount config/producers.yaml)` }, { status: 503 });
  }
  let blocked: string | null = null;
  try {
    blocked = demoAccountBlocked();
  } catch (e) {
    return NextResponse.json({ ok: false, service: "web", detail: `producer roster unreadable: ${(e as Error).message}` }, { status: 503 });
  }
  if (blocked) return NextResponse.json({ ok: false, service: "web", detail: blocked }, { status: 503 });
  return NextResponse.json({ ok: true, service: "web" });
}
export const dynamic = "force-dynamic";
