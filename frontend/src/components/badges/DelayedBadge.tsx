import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";

interface DelayedBadgeProps {
  className?: string;
  /** 默认 true；传 false 则不渲染（方便条件 mount） */
  show?: boolean;
}

export function DelayedBadge({ className, show = true }: DelayedBadgeProps) {
  if (!show) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border border-red-200 bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700",
        className,
      )}
    >
      <AlertTriangle className="h-3 w-3" />
      已延期
    </span>
  );
}
