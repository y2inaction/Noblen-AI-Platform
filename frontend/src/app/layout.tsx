import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Noblen AI Platform",
  description:
    "Modular, multi-tenant AI business-automation platform by Noblen AI Solutions.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
