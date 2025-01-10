"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  HardDrive,
  Network,
  Shield,
  Settings,
  Server,
} from "lucide-react";

const navItems = [
  {
    title: "VM Management",
    href: "/vm-management",
    icon: Server,
  },
  {
    title: "VPC Management",
    href: "/vpc-management",
    icon: Network,
  },
  {
    title: "Disk Management",
    href: "/disk-management",
    icon: HardDrive,
  },
  {
    title: "Firewall",
    href: "/firewall",
    icon: Shield,
  },
  {
    title: "Settings",
    href: "/settings",
    icon: Settings,
  },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="border-b">
      <div className="container mx-auto">
        <div className="flex h-16 items-center px-4">
          <div className="flex items-center space-x-4">
            {navItems.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center space-x-2 px-3 py-2 text-sm font-medium rounded-md transition-colors",
                  pathname === item.href
                    ? "bg-primary text-primary-foreground"
                    : "hover:bg-muted"
                )}
              >
                <item.icon className="h-4 w-4" />
                <span>{item.title}</span>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </nav>
  );
} 