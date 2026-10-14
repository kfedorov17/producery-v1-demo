import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

/** UI-8 (documents): OneDrive manual mode — upload a markdown document, see it ingested on Documents, find it by search. */
test("upload a markdown document through the OneDrive card; Documents shows it ready and search finds it", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  await page.goto("/app/connections");
  const card = page.getByTestId("connection-onedrive");
  const stamp = Date.now().toString(36);
  await card.getByLabel("Document title").fill(`Validation help article ${stamp}`);
  await card.getByLabel("Document type").selectOption("help");
  await card.getByLabel("Document file").setInputFiles({
    name: `validation-${stamp}.md`,
    mimeType: "text/markdown",
    buffer: Buffer.from(
      `# Validation help article ${stamp}\n\n## Roof age rule\n\nHomes with a roof older than 20 years need a roof inspection before a homeowners renewal is remarketed. Token: ${stamp}.\n\n## Flood\n\nFlood is excluded from the standard homeowners form; a separate NFIP or private flood policy is required.\n`,
    ),
  });
  await card.getByRole("button", { name: "Upload document" }).click();
  const note = card.getByRole("status");
  await expect(note).toContainText(/Stored as doc-/, { timeout: 15_000 });
  const docId = (await note.textContent())!.match(/(doc-[\w-]+)/)![1]!;

  // ingestion runs on the worker; poll the Documents list until the row is ready
  const deadline = Date.now() + 120_000;
  let status = "";
  while (Date.now() < deadline) {
    const res = await page.request.get("/api/ui/documents");
    const data = (await res.json()) as { items: { id: string; status: string; chunk_count: number }[] };
    const row = data.items.find((d) => d.id === docId);
    status = row?.status ?? "";
    if (status === "ready" && (row?.chunk_count ?? 0) > 0) break;
    await page.waitForTimeout(3_000);
  }
  expect(status).toBe("ready");

  await page.goto(`/app/documents?q=${encodeURIComponent(`roof inspection ${stamp}`)}`);
  await expect(page.getByTestId("doc-row").filter({ hasText: docId })).toBeVisible();
  await expect(page.getByTestId("search-hit").filter({ hasText: docId }).first()).toBeVisible({ timeout: 30_000 });
});
