import type { Metadata } from "next";
import "./globals.css";

const publicUrl = process.env.OOPZ_PANEL_PUBLIC_URL || "http://localhost:3000";
const title = "OOPZ Control — 机器人管理面板";
const description = "Oopzbot 音乐、语音频道与任务状态管理面板。";

export const metadata: Metadata = {
  metadataBase: new URL(publicUrl),
  title,
  description,
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
  openGraph: {
    title,
    description,
    type: "website",
    images: [{ url: "/og.png", width: 1536, height: 1024, alt: "OOPZ Control 机器人管理面板" }],
  },
  twitter: { card: "summary_large_image", title, description, images: ["/og.png"] },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
