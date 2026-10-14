import { expect, test } from "@playwright/test";
import { DEMO, signIn } from "./helpers";

test("unauthenticated /app redirects to /login and the proxy answers 401", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login/);
  const res = await page.request.get("/api/ui/inbox");
  expect(res.status()).toBe(401);
});

test("wrong password is refused; right one lands on Today with the producer's name", async ({ page }) => {
  await page.goto("/login");
  await page.getByPlaceholder("Producer name").fill(DEMO.name);
  await page.getByPlaceholder("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Wrong name or password")).toBeVisible(); // Next adds its own role=alert route announcer
  await signIn(page);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Good day");
});

test("the browser never receives the agent token; the proxy allowlist blocks unknown paths", async ({ page }) => {
  await signIn(page);
  const html = await page.content();
  expect(html).not.toMatch(/Bearer\s+[A-Za-z0-9_-]{20,}/);
  const res = await page.request.post("/api/ui/runs", { data: {} });
  expect(res.status()).toBe(403);
});

test("sign out clears the session", async ({ page }) => {
  await signIn(page);
  await page.goto("/app/settings");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await page.request.get("/api/ui/inbox")).status()).toBe(401);
});
