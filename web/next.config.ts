import type { NextConfig } from "next";

/** Producer UI. Standalone output in the container build (Linux); local Windows builds skip it because standalone
 *  tracing needs symlink privileges Windows does not grant by default. */
const nextConfig: NextConfig = {
  ...(process.env.NEXT_STANDALONE === "1" ? { output: "standalone" as const } : {}),
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
