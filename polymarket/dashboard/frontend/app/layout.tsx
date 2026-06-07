import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Mad Scientist — Polymarket Bot",
  description: "Live trading dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" style={{ height: "100%", background: "#111" }}>
      <body style={{ height: "100%", margin: 0 }}>{children}</body>
    </html>
  );
}
