import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "@copilotkit/react-core/v2/styles.css";
import "./globals.css";
import "./copilotkit-theme.css";

/** Clean corporate sans — in the same spirit as large energy / B2B marketing sites */
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "TTO Checklist UI",
  description: "CopilotKit + Next.js UI for an AG-UI compatible TTO agent.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`dark ${inter.variable}`}>
      <body className={inter.className}>{children}</body>
    </html>
  );
}
