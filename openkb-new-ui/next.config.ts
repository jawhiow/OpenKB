import type { NextConfig } from "next";

const apiTarget = (process.env.OPENKB_API_TARGET ?? "http://127.0.0.1:8765")
  .replace(/\/api\/?$/, "")
  .replace(/\/$/, "");
const allowedDevOrigins = (process.env.OPENKB_ALLOWED_DEV_ORIGINS ?? "127.0.0.1,localhost,100.66.1.2")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  allowedDevOrigins,
  devIndicators: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiTarget}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
