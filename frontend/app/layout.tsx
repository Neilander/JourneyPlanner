import type { Metadata } from "next";
import { ZCOOL_KuaiLe } from "next/font/google";
import "./globals.css";

const zcoolKuaiLe = ZCOOL_KuaiLe({
  weight: "400",
  subsets: ["chinese-simplified"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "旅途向导",
  description: "酒店通勤助手",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className={`${zcoolKuaiLe.className} min-h-full flex flex-col`}>{children}</body>
    </html>
  );
}
