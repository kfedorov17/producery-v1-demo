#!/usr/bin/env node
// Rotate (or add) a producer in config/producers.yaml: prints the new password ONCE, stores only the bcrypt hash.
// Usage: node scripts/rotate-producer.mjs <name> [display name] [--file path]
import { readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
import bcrypt from "bcryptjs";
import { parse, stringify } from "yaml";

const args = process.argv.slice(2);
const fileIdx = args.indexOf("--file");
const file = fileIdx >= 0 ? args.splice(fileIdx, 2)[1] : resolve(process.cwd(), "..", "config", "producers.yaml");
const [name, ...rest] = args;
if (!name) {
  console.error("usage: rotate-producer.mjs <name> [display name] [--file path]");
  process.exit(2);
}
const display = rest.join(" ") || name;
const doc = parse(readFileSync(file, "utf8")) ?? {};
doc.producers = doc.producers ?? [];
const password = randomBytes(12).toString("base64url");
const password_hash = bcrypt.hashSync(password, 10);
const row = doc.producers.find((p) => String(p.name).toLowerCase() === name.toLowerCase());
if (row) row.password_hash = password_hash;
else doc.producers.push({ name: name.toLowerCase(), display_name: display, password_hash });
const header = "# Producer roster for the web UI (docs/design.md section 14). No sign-up and no admin screen: this file IS the account list.\n# Rotate or add an account: `make producer NAME=<name>` (prints the password once, stores only the bcrypt hash).\n";
writeFileSync(file, header + stringify(doc), "utf8");
console.log(`producer ${name.toLowerCase()} ${row ? "rotated" : "added"} in ${file}`);
console.log(`password (shown once): ${password}`);
