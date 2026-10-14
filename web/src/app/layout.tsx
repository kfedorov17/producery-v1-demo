import type { Metadata } from "next";
import type { ReactNode } from "react";
import "@/styles/tokens.css";

export const metadata: Metadata = {
  title: "Producery",
  description: "The approval door for your inbound crew.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-bg font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
