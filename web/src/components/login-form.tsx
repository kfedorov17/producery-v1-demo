"use client";
/** Sign-in name + password against config/producers.yaml. No social
 *  sign-in, no sign-up, no splash. */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ProduceryLogo, Button } from "@/components/ui";

export function LoginForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, password }) });
    setBusy(false);
    if (!res.ok) {
      setError(res.status === 429 ? "Too many attempts — wait a minute" : "Wrong name or password");
      return;
    }
    const next = new URLSearchParams(window.location.search).get("next");
    router.push(next && next.startsWith("/app") ? next : "/app");
    router.refresh();
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-4">
      <ProduceryLogo size={28} />
      <p className="max-w-sm text-center text-muted">The approval door for your inbound crew. Nothing goes out without you.</p>
      <form onSubmit={submit} className="card flex w-full max-w-sm flex-col gap-3">
        <input className="input" placeholder="Producer name" autoComplete="username" required value={name} onChange={(e) => setName(e.target.value)} />
        <input className="input" placeholder="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" disabled={busy}>{busy ? "…" : "Sign in"}</Button>
        {error && <div className="rounded-btn bg-danger-bg px-3 py-2 text-sm text-danger-fg" role="alert">{error}</div>}
      </form>
      <p className="disclaimer max-w-md text-center">
        Every client-facing message is drafted by software and approved by a licensed producer. Producery never
        quotes, rates, binds, or gives coverage advice.
      </p>
    </div>
  );
}
