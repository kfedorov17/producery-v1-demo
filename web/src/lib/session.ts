import "server-only";
/** Producer session: an encrypted HttpOnly cookie (iron-session) carrying the signed-in producer's name. The server is
 *  the security boundary; the browser never sees the agent token. Accounts come from config/producers.yaml. */
import { cookies } from "next/headers";
import { getIronSession, type IronSession } from "iron-session";
import { redirect } from "next/navigation";

export interface SessionData {
  name?: string;
  displayName?: string;
  role?: "producer";
  signedInAt?: string;
}

export interface Producer {
  name: string;
  displayName: string;
  role: "producer";
}

const COOKIE = "producery_session";

function password(): string {
  const secret = process.env.SESSION_SECRET;
  if (!secret || secret.length < 32) throw new Error("SESSION_SECRET must be set (32+ characters)");
  return secret;
}

export async function getSession(): Promise<IronSession<SessionData>> {
  return getIronSession<SessionData>(await cookies(), {
    password: password(),
    cookieName: COOKIE,
    ttl: 60 * 60 * 12, // the seal expires with the cookie, not after iron-session's 14-day default
    cookieOptions: {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.COOKIE_SECURE !== "0" && process.env.NODE_ENV === "production",
      path: "/",
    },
  });
}

export async function currentProducer(): Promise<Producer | null> {
  const s = await getSession();
  if (!s.name) return null;
  return { name: s.name, displayName: s.displayName ?? s.name, role: "producer" };
}

/** Server components and route handlers call this; unauthenticated → /login. */
export async function requireProducer(): Promise<Producer> {
  const p = await currentProducer();
  if (!p) redirect("/login");
  return p;
}
