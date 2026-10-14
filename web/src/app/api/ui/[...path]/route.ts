/** The browser's only door to the agent API (design.md §14). Session-gated, allowlisted by method + path, and
 *  the bearer token never leaves the server. Decisions are stamped with the signed-in producer: `by` on resume
 *  bodies is overwritten with the session name, and every call carries `x-producery-user`. */
import { NextResponse } from "next/server";
import { isAllowed, isFeedback, isResume, timeoutFor } from "@/lib/allowlist";
import { agentFetch, AgentError } from "@/lib/api";
import { currentProducer } from "@/lib/session";

async function handle(req: Request, params: Promise<{ path: string[] }>) {
  const producer = await currentProducer();
  if (!producer) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  const { path } = await params;
  const rel = path.map((p) => encodeURIComponent(decodeURIComponent(p))).join("/");
  const plain = path.map((p) => decodeURIComponent(p)).join("/");
  const method = req.method.toUpperCase();
  if (!isAllowed(method, plain)) {
    return NextResponse.json({ detail: `not allowed: ${method} /${plain}` }, { status: 403 });
  }
  const search = new URL(req.url).search;
  let body: unknown;
  if (method !== "GET") {
    body = await req.json().catch(() => ({}));
    if (isResume(plain) && body && typeof body === "object") {
      (body as Record<string, unknown>).by = producer.name; // the audit trail names the producer, not the browser
    }
    if (isFeedback(plain) && body && typeof body === "object") {
      delete (body as Record<string, unknown>).trace_id; // the agent resolves the thread's own trace; the browser never picks the run
    }
  }
  try {
    const data = await agentFetch(`/${rel}${search}`, { method, body, user: producer.name, timeoutMs: timeoutFor(plain) });
    return NextResponse.json(data);
  } catch (e) {
    if (e instanceof AgentError) return NextResponse.json({ detail: e.message }, { status: e.status });
    return NextResponse.json({ detail: e instanceof Error ? e.message : "upstream error" }, { status: 502 });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };
export const GET = (req: Request, ctx: Ctx) => handle(req, ctx.params);
export const POST = (req: Request, ctx: Ctx) => handle(req, ctx.params);
export const PUT = (req: Request, ctx: Ctx) => handle(req, ctx.params);
export const dynamic = "force-dynamic";
