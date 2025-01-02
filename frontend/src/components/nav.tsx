import Link from "next/link";

export function MainNav() {
  return (
    <div className="flex items-center space-x-4 lg:space-x-6">
      <Link
        href="/"
        className="text-sm font-medium transition-colors hover:text-primary"
      >
        Overview
      </Link>
      <Link
        href="/vpc-management"
        className="text-sm font-medium text-muted-foreground transition-colors hover:text-primary"
      >
        VPC Management
      </Link>
      <Link
        href="/vm-management"
        className="text-sm font-medium text-muted-foreground transition-colors hover:text-primary"
      >
        VM Management
      </Link>
      <Link
        href="/disk-management"
        className="text-sm font-medium text-muted-foreground transition-colors hover:text-primary"
      >
        Disk Management
      </Link>
      <Link
        href="/ip-management"
        className="text-sm font-medium text-muted-foreground transition-colors hover:text-primary"
      >
        IP Management
      </Link>
    </div>
  );
} 