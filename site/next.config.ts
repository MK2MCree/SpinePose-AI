import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    proxyClientMaxBodySize: "64mb",
    serverActions: {
      bodySizeLimit: "64mb",
    },
  },
};

export default nextConfig;
