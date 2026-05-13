import { cn } from "@/lib/utils";

interface PriorityBadgeProps {
  priority: number | null | undefined;
  className?: string;
}

const PRIORITY_META: Record<number, { label: string; cls: string }> = {
  0: { label: "紧急", cls: "bg-red-100 text-red-700 border-red-200" },
  1: { label: "高", cls: "bg-orange-100 text-orange-700 border-orange-200" },
  2: { label: "中", cls: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  3: { label: "低", cls: "bg-slate-100 text-slate-600 border-slate-200" },
};

export function PriorityBadge({ priority, className }: PriorityBadgeProps) {
  const meta = priority != null ? PRIORITY_META[priority] : undefined;
  if (!meta) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded border px-1.5 py-0.5 text-xs",
          "bg-slate-100 text-slate-600 border-slate-200",
          className,
        )}
      >
        -
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium",
        meta.cls,
        className,
      )}
    >
      {meta.label}
    </span>
  );
}
