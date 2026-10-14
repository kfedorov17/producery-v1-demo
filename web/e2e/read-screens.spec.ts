import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("Clients shows the allowlisted views only and searches a view", async ({ page }) => {
  await page.goto("/app/clients");
  await expect(page.getByTestId("book-table")).toBeVisible();
  await page.goto("/app/clients?view=v_renewals&q=HO-1001");
  await expect(page.getByTestId("book-table")).toContainText("v_renewals");
  // a non-allowlisted view falls back to households (never a raw table)
  await page.goto("/app/clients?view=inbox");
  await expect(page.getByTestId("book-table")).toContainText("v_households");
});

test("Documents lists the library and search returns cited chunks", async ({ page }) => {
  await page.goto("/app/documents");
  await expect(page.getByTestId("doc-row").first()).toBeVisible();
  await page.getByLabel("Search documents").fill("roof age");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByTestId("search-hit").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("search-hit").first()).toContainText("[doc:");
});

test("Settings shows the crew, prompt pins and readiness", async ({ page }) => {
  await page.goto("/app/settings");
  await expect(page.getByTestId("agent-card").first()).toBeVisible();
  await expect(page.getByText("prompts_pinned")).toBeVisible();
});

test("Connections persists a ticked manual task", async ({ page }) => {
  await page.goto("/app/connections");
  const box = page.getByTestId("connection-onedrive").getByRole("checkbox").first();
  const before = await box.isChecked();
  await box.setChecked(!before);
  await page.waitForTimeout(500);
  await page.reload();
  await expect(page.getByTestId("connection-onedrive").getByRole("checkbox").first()).toBeChecked({ checked: !before });
  await page.getByTestId("connection-onedrive").getByRole("checkbox").first().setChecked(before); // leave as found
});
