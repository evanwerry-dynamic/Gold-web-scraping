import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  // basePath only needed when deploying to a sub-path (e.g. GitHub Pages)
  basePath: process.env.NEXT_PUBLIC_BASE_PATH ?? "",
  images: { unoptimized: true },
};

export default nextConfig;
