import "server-only";
/** Producer accounts: config/producers.yaml (name, display_name, bcrypt password_hash). No sign-up, no admin role —
 *  the file is the roster. `PRODUCERS_FILE` overrides the path (the compose service mounts the repo file read-only). */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import bcrypt from "bcryptjs";
import { parse } from "yaml";

export interface ProducerAccount {
  name: string;
  display_name: string;
  password_hash: string;
}

/** The hash shipped in config/producers.yaml for the demo producer (password in docs/cookbook/first-turn.md). A deployment that
 *  terminates TLS (COOKIE_SECURE=1) refuses to serve while this hash is still in the roster: `make producer` rotates it. */
export const DEMO_HASH = "$2a$10$b4NpI.08ttAltw0vDbWOPOgmiO46Re8zC9WLgPe671vizM5QpbdDm";

export function demoAccountBlocked(): string | null {
  if (process.env.COOKIE_SECURE !== "1") return null;
  const demo = loadProducers().find((p) => p.password_hash === DEMO_HASH);
  return demo ? `demo producer "${demo.name}" still has the published password — rotate it with \`make producer NAME=${demo.name}\`` : null;
}

let cache: { at: number; rows: ProducerAccount[] } | null = null;

export function loadProducers(): ProducerAccount[] {
  const now = Date.now();
  if (cache && now - cache.at < 30_000) return cache.rows;
  const file = process.env.PRODUCERS_FILE ?? resolve(process.cwd(), "..", "config", "producers.yaml");
  const raw = parse(readFileSync(file, "utf8")) as { producers?: ProducerAccount[] };
  const rows = (raw?.producers ?? []).filter((p) => p.name && p.password_hash);
  cache = { at: now, rows };
  return rows;
}

export async function verifyProducer(name: string, password: string): Promise<ProducerAccount | null> {
  const row = loadProducers().find((p) => p.name.toLowerCase() === name.trim().toLowerCase());
  if (!row) {
    await bcrypt.compare(password, "$2a$10$CwTycUXWue0Thq9StjUM0uJ8hG3f3iKpT4bG5B3o8kqj2p6r7G2kK"); // constant-time-ish miss
    return null;
  }
  return (await bcrypt.compare(password, row.password_hash)) ? row : null;
}
