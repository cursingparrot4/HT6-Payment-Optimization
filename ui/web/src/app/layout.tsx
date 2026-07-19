import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "CardIQ — smarter card routing for big recurring payments",
  description:
    "CardIQ reevaluates which credit card should fund each large recurring payment and safely recommends switches. Synthetic data only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-sans">
        <div className="min-h-screen">
          <Sidebar />
          <main className="px-4 py-8 sm:px-6 lg:px-8">
            <div className="mx-auto max-w-7xl">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
