import type { NextConfig } from "next";

const isPages = process.env.PAGES_BUILD === "true";

const nextConfig: NextConfig = {
  output: isPages ? "export" : "standalone",
  basePath: isPages ? (process.env.PAGES_BASE_PATH ?? "/Gold-web-scraping") : "",
  ...(isPages && { images: { unoptimized: true } }),
};

export default nextConfig;
