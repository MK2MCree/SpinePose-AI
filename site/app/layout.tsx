import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "脊见智诊 SpineInsight｜脊柱智能辅助筛查与分析平台",
  description: "面向 X 光、DICOM 与 RGB 体表图像的脊柱智能辅助筛查、量化分析和双版本报告平台。",
  icons: {
    icon: "/favicon.png",
    shortcut: "/favicon.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
