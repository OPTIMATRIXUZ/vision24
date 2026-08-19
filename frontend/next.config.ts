import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8020";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
  },

  experimental: {
    proxyClientMaxBodySize: "2gb",
  },

  output: "standalone",

  outputFileTracingRoot: projectRoot,

  images: {
    unoptimized: true,
  },
};

export default nextConfig;
