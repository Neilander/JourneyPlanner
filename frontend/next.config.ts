import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",              // 烤成静态文件（便当），交给 Cloudflare Pages 托管
  images: { unoptimized: true }, // 静态导出下必须关掉 Next 的图片优化
};

export default nextConfig;
