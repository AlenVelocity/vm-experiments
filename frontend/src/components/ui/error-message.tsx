import { XCircle } from "lucide-react";

interface ErrorMessageProps {
  message: string;
}

export function ErrorMessage({ message }: ErrorMessageProps) {
  return (
    <div className="flex items-center gap-2 text-red-500 text-sm">
      <XCircle className="h-4 w-4" />
      <span>{message}</span>
    </div>
  );
} 