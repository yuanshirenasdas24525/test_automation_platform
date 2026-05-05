/**
 * 工作台共享 UI：WidgetCard + 任务行渲染。
 *
 * 各角色 workspace 都长得差不多——一组 "title + 计数 + 列表（最多 N 行）+ 查看全部"
 * 的卡片栅格。这里收口一份 dumb component，避免每个 workspace 文件重写。
 */
import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Task } from "@/types/domain";

const DEFAULT_PREVIEW = 5;

interface WidgetCardProps<T> {
  title: string;
  /** 列表数据；undefined = 加载中（自动渲染 spinner） */
  items: T[] | undefined;
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  /** 单行渲染。`item` 是单条数据；返回值会包在 <li> 里。 */
  renderItem: (item: T) => ReactNode;
  /** 列表为空时的占位提示。 */
  emptyText?: string;
  /** 顶部右侧"查看全部"链接（一般指 /tasks?...）。 */
  viewAllHref?: string;
  /** 预览行数，超过截断；默认 5。 */
  previewLimit?: number;
  /** 显式覆盖右上角计数（不传时用 items.length）。 */
  count?: number;
  /** 顶部副标题/筛选条（可选）。 */
  subtitle?: ReactNode;
  className?: string;
}

export function WidgetCard<T>({
  title,
  items,
  isLoading,
  isError,
  errorMessage,
  renderItem,
  emptyText = "暂无",
  viewAllHref,
  previewLimit = DEFAULT_PREVIEW,
  count,
  subtitle,
  className,
}: WidgetCardProps<T>) {
  const total = count ?? items?.length ?? 0;
  const preview = (items ?? []).slice(0, previewLimit);
  const remaining = (items?.length ?? 0) - preview.length;

  return (
    <Card className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold">{title}</h3>
            <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
              {isLoading ? "…" : total}
            </span>
          </div>
          {subtitle ? (
            <div className="mt-0.5 text-xs text-muted-foreground">{subtitle}</div>
          ) : null}
        </div>
        {viewAllHref ? (
          <Link
            to={viewAllHref}
            className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
          >
            查看全部 →
          </Link>
        ) : null}
      </div>
      <CardContent className="flex-1 p-0">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 px-4 py-6 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载中
          </div>
        ) : isError ? (
          <div className="px-4 py-6 text-xs text-destructive">
            加载失败：{errorMessage ?? "未知错误"}
          </div>
        ) : preview.length === 0 ? (
          <div className="px-4 py-6 text-xs text-muted-foreground">
            {emptyText}
          </div>
        ) : (
          <ul className="divide-y">
            {preview.map((item, idx) => (
              <li key={idx}>{renderItem(item)}</li>
            ))}
            {remaining > 0 && viewAllHref ? (
              <li className="px-4 py-2 text-center text-xs text-muted-foreground">
                <Link
                  to={viewAllHref}
                  className="hover:text-foreground"
                >
                  还有 {remaining} 条…
                </Link>
              </li>
            ) : null}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** 工作台里的"任务行"——title 主行 + 元信息副行；点击跳详情。 */
export function TaskRow({ task }: { task: Task }) {
  return (
    <Link
      to={`/tasks/${task.id}`}
      className="block px-4 py-2 text-sm hover:bg-accent/40"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-medium">{task.title}</span>
        <span
          className={cn(
            "shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
            statusColor(task.status),
          )}
        >
          {task.status}
        </span>
      </div>
      <div className="mt-0.5 truncate text-xs text-muted-foreground">
        {task.type}
        {task.severity ? ` · ${task.severity}` : ""}
        {task.requirement_id ? ` · req#${task.requirement_id}` : ""}
      </div>
    </Link>
  );
}

function statusColor(status: string): string {
  switch (status) {
    case "passed":
    case "closed":
      return "bg-green-100 text-green-800";
    case "failed":
      return "bg-red-100 text-red-800";
    case "dev_doing":
    case "test_doing":
      return "bg-blue-100 text-blue-800";
    case "dev_done":
      return "bg-amber-100 text-amber-800";
    default:
      return "bg-muted text-muted-foreground";
  }
}

/** 把当天 00:00 转 ISO 字符串，给 closed_at_after 用。 */
export function startOfTodayIso(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

/** 工作台页面的统一外壳：顶部标题 + grid 容器。 */
export function WorkspaceShell({
  title,
  description,
  switcher,
  children,
}: {
  title: string;
  description?: string;
  switcher?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-4 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{title}</h1>
          {description ? (
            <p className="text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {switcher}
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{children}</div>
    </div>
  );
}
