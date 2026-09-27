import type { Metadata } from "next";
import { connection } from "next/server";
import "./globals.css";

export const metadata: Metadata = {
  title: "Noblen AI Platform",
  description:
    "Modular, multi-tenant AI business-automation platform by Noblen AI Solutions.",
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Render per request so each response carries a fresh CSP nonce (src/proxy.ts).
  await connection();
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
