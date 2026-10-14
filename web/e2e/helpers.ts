import { expect, type Page } from "@playwright/test";

export const DEMO = { name: process.env.E2E_USER ?? "sam", password: process.env.E2E_PASSWORD ?? "producery-demo" };

export async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder("Producer name").fill(DEMO.name);
  await page.getByPlaceholder("Password").fill(DEMO.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/app$/, { timeout: 30_000 });
}

/** Capture an email through the UI's manual-mode door and wait until the worker pauses it for a decision. */
export async function captureAndWaitPaused(page: Page, email: { from: string; name: string; subject: string; body: string }) {
  await page.goto("/app/connections");
  const card = page.getByTestId("connection-email");
  await card.getByLabel("From email").fill(email.from);
  await card.getByLabel("From name").fill(email.name);
  await card.getByLabel("Subject").fill(email.subject);
  await card.getByLabel("Email body").fill(email.body);
  await card.getByRole("button", { name: "Capture email" }).click();
  const note = card.getByRole("status");
  await expect(note).toContainText("Captured as", { timeout: 15_000 });
  const threadId = (await note.textContent())!.match(/Captured as (\S+)\./)![1]!;
  // poll the thread until it is paused (the worker + crew usually take 20–90 s with the CLI bridge)
  const deadline = Date.now() + 240_000;
  while (Date.now() < deadline) {
    const res = await page.request.get(`/api/ui/threads/${encodeURIComponent(threadId)}`);
    const data = (await res.json()) as { status?: string };
    if (data.status === "paused") return threadId;
    if (data.status === "failed") throw new Error(`thread ${threadId} failed`);
    await page.waitForTimeout(5_000);
  }
  throw new Error(`thread ${threadId} did not pause in time`);
}
