// Usage: node scripts/hash-password.mjs <password>  → prints a bcrypt hash for config/producers.yaml
import bcrypt from "bcryptjs";

const pw = process.argv[2];
if (!pw || pw.length < 8) {
  console.error("usage: node scripts/hash-password.mjs <password of 8+ characters>");
  process.exit(2);
}
console.log(bcrypt.hashSync(pw, 10));
