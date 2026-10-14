import type { ReactNode } from "react";
import { AppShell } from "@/components/shell";
import { requireProducer } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: ReactNode }) {
  const user = await requireProducer();
  return <AppShell user={user}>{children}</AppShell>;
}
