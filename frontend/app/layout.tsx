import type { Metadata, Viewport } from "next";
import { ZCOOL_KuaiLe } from "next/font/google";
import "./globals.css";

const zcoolKuaiLe = ZCOOL_KuaiLe({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});

export const metadata: Metadata = {
  title: "旅途向导",
  description: "酒店出行助手",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased" suppressHydrationWarning>
      <body className={`${zcoolKuaiLe.variable} min-h-full flex flex-col`} style={{ fontFamily: "'LXGW WenKai Screen', 'LXGW WenKai', serif" }}>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/lxgw-wenkai-screen-webfont@1.1.0/style.css" />
        {children}
      </body>
    </html>
  );
}
