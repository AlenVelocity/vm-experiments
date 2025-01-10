"use client";

import { Nav } from "@/components/nav";

interface MainLayoutProps {
  children: React.ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  return (
    <div className="min-h-screen bg-background">
      <Nav />
      <main className="container mx-auto py-6">{children}</main>
    </div>
  );
} 