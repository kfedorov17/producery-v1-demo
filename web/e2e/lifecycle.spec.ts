import { expect, test } from "@playwright/test";
import { captureAndWaitPaused, signIn } from "./helpers";

/** The producer lifecycle end to end through the UI: paste → crew runs → decision card → approve → outbox → thread.
 *  Needs a working model door; hosted CI has none (the eval gate is RED there by design), so these skip when CI is
 *  set unless E2E_LIFECYCLE=1 opts in. */
test.skip(() => !!process.env.CI && process.env.E2E_LIFECYCLE !== "1", "needs the model door (set E2E_LIFECYCLE=1)");
test.describe.configure({ timeout: 360_000 }); // a crew turn through the CLI bridge can take minutes, plus worker retries
test("paste an email, approve the proposal, see it in Recently sent and the audit trail", async ({ page }) => {
  await signIn(page);
  const stamp = Date.now().toString(36);
  const threadId = await captureAndWaitPaused(page, {
    from: "jordan.lee@example.com",
    name: "Jordan Lee",
    subject: `Question about my renewal (${stamp})`,
    body: "Hi, when does my homeowners policy renew and did the premium change? Thanks, Jordan",
  });

  await page.goto("/app/approve");
  const card = page.getByTestId("approval-card").filter({ hasText: stamp });
  await expect(card).toBeVisible();
  await expect(card.getByText("Owner brief")).toBeVisible();
  await card.getByRole("button", { name: "Approve", exact: true }).click();
  const decided = page.getByTestId("decided-card").filter({ hasText: stamp });
  await expect(decided).toBeVisible({ timeout: 90_000 });

  await page.goto(`/app/threads/${encodeURIComponent(threadId)}`);
  await expect(page.getByText("Audit trail")).toBeVisible();
  await expect(page.locator("table").first()).toContainText(/decision|proposal_approved|approved/);
  await expect(page.getByText(/decided by sam/)).toBeVisible();
});

test("reject with a reason is audited under the producer's name", async ({ page }) => {
  await signIn(page);
  const stamp = Date.now().toString(36);
  const threadId = await captureAndWaitPaused(page, {
    from: "casey.kim@example.com",
    name: "Casey Kim",
    subject: `Add my new car (${stamp})`,
    body: "Hello, I bought a 2024 Subaru Outback yesterday. Can you add it to my auto policy? VIN to follow.",
  });
  await page.goto("/app/approve");
  const card = page.getByTestId("approval-card").filter({ hasText: stamp });
  await card.getByRole("button", { name: "Reject" }).click();
  await expect(card.getByRole("button", { name: "Confirm reject" })).toBeDisabled();
  await card.getByLabel("Rejection reason").fill("Call the client first");
  await card.getByRole("button", { name: "Confirm reject" }).click();
  await expect(page.getByTestId("decided-card").filter({ hasText: stamp })).toContainText("rejected", { timeout: 90_000 });
  await page.goto(`/app/threads/${encodeURIComponent(threadId)}`);
  await expect(page.getByText(/decided by sam: reject/)).toBeVisible();
});
