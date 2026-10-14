/** The proxy's allowlist (design.md §14): method + path pairs the browser may reach on the agent API, and the
 *  per-route timeout. Pure functions so they are unit-tested without a server. */
const ALLOW: { method: string; re: RegExp }[] = [
  { method: "GET", re: /^inbox$/ },
  { method: "POST", re: /^inbox$/ },
  { method: "GET", re: /^proposals$/ },
  { method: "GET", re: /^outbox$/ },
  { method: "GET", re: /^book\/v_[a-z_]+$/ },
  { method: "GET", re: /^documents$/ },
  { method: "POST", re: /^documents$/ },
  { method: "GET", re: /^documents\/search$/ },
  { method: "GET", re: /^connections$/ },
  { method: "PUT", re: /^connections\/(email|onedrive|gdrive)$/ },
  { method: "GET", re: /^threads\/crew:[^/]+$/ }, // production threads only — eval:/smoke: threads are not the producer's
  { method: "GET", re: /^threads\/crew:[^/]+\/(audit|history)$/ },
  { method: "POST", re: /^threads\/crew:[^/]+\/(resume|feedback|continue)$/ },
  { method: "GET", re: /^agents$/ },
  { method: "GET", re: /^ready$/ },
];

export function isAllowed(method: string, path: string): boolean {
  const m = method.toUpperCase();
  return ALLOW.some((a) => a.method === m && a.re.test(path));
}

export function isFeedback(path: string): boolean {
  return /^threads\/[^/]+\/feedback$/.test(path);
}

export function isResume(path: string): boolean {
  return /^threads\/[^/]+\/resume$/.test(path);
}

/** A resume executes without a model call; a continue can run the drafter for minutes through the interim door. */
export function timeoutFor(path: string): number {
  if (/^threads\/[^/]+\/continue$/.test(path)) return 300_000;
  if (isResume(path)) return 120_000;
  return 30_000;
}
