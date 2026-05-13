import { cn } from "@/lib/utils";
import type { RequirementSystemStatus } from "@/types/domain";

interface RequirementStatusBadgeProps {
  status: RequirementSystemStatus | null | undefined;
  className?: string;
}

const STATUS_META: Record<
  RequirementSystemStatus,
  { label: string; cls: string }
> = {
  approved: { label: "待开发", cls: "bg-slate-100 text-slate-700 border-slate-200" },
  developing: { label: "开发中", cls: "bg-blue-100 text-blue-700 border-blue-200" },
  pm_review: { label: "产品体验", cls: "bg-purple-100 text-purple-700 border-purple-200" },
  testing: { label: "测试中", cls: "bg-amber-100 text-amber-700 border-amber-200" },
  ready_to_release: { label: "待发布", cls: "bg-teal-100 text-teal-700 border-teal-200" },
  done: { label: "已完成", cls: "bg-emerald-100 text-emerald-700 border-emerald-200" },
};

export function RequirementStatusBadge({
  status,
  className,
}: RequirementStatusBadgeProps) {
  const meta = status ? STATUS_META[status] : undefined;
  if (!meta) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded border px-1.5 py-0.5 text-xs",
          "bg-slate-50 text-slate-500 border-slate-200",
          className,
        )}
      >
        未启动
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
