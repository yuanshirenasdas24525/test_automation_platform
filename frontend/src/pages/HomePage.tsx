import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  FolderKanban,
  HelpCircle,
  PlayCircle,
  RefreshCw,
  Smartphone,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  systemApi,
  type ServiceHealth,
  type ServiceStatusItem,
  type OverallHealth,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";

export function HomePage() {
  return (
    <div className="space-y-6 p-8">
      <div>
        <h1 className="text-2xl font-semibold">工作台</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          欢迎回来。这里是平台总览，可以快速进入各个模块，并实时查看依赖服务状态。
        </p>
      </div>

      <ServiceStatusCard />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <QuickCard
          to="/projects?type=api"
          title="API 项目"
          desc="管理接口自动化项目，创建/编辑/删除"
          icon={FolderKanban}
        />
        <QuickCard
          to="/projects?type=app"
          title="App 项目"
          desc="管理 Appium 自动化项目"
          icon={Smartphone}
        />
        <QuickCard
          to="/runs"
          title="执行记录"
          desc="查看历史执行报告，支持按项目 / 状态过滤"
          icon={PlayCircle}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 服务状态卡片
// ---------------------------------------------------------------------------
function ServiceStatusCard() {
  const { data, isLoading, isFetching, refetch, error } = useQuery({
    queryKey: queryKeys.systemServices(),
    queryFn: () => systemApi.services(),
    refetchInterval: 15_000, // 每 15s 自动刷一次
    staleTime: 5_000,
    retry: 0, // 系统检查失败不要狂重试，直接显示错误
  });

  const overall = data?.overall ?? "down";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-3">
          <CardTitle className="text-base">服务状态</CardTitle>
          <OverallBadge overall={isLoading ? "unknown" : overall} />
          {data?.checked_at && (
            <span className="text-xs text-muted-foreground">
              检查于 {formatTime(data.checked_at)}
            </span>
          )}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetch()}
          disabled={isFetching}
        >
          <RefreshCw
            className={`mr-1 h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`}
          />
          刷新
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            无法获取服务状态：{(error as Error).message}
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(data?.services ?? PLACEHOLDER_SERVICES).map((s) => (
              <ServiceRow key={s.key} svc={s} loading={isLoading} />
            ))}
          </div>
        )}
        {overall === "down" && (
          <p className="mt-4 text-xs text-muted-foreground">
            提示：必需服务未就绪，可能导致用例执行无法正常完成。常见启动命令：
            <code className="mx-1 rounded bg-muted px-1.5 py-0.5">
              celery -A celery_app worker --loglevel=INFO
            </code>
            （需要先启动 Redis）
          </p>
        )}
      </CardContent>
    </Card>
  );
}

const PLACEHOLDER_SERVICES: ServiceStatusItem[] = [
  { key: "fastapi", name: "FastAPI 后端", required: true, status: "unknown", detail: "", latency_ms: null },
  { key: "database", name: "数据库", required: true, status: "unknown", detail: "", latency_ms: null },
  { key: "redis", name: "Redis", required: true, status: "unknown", detail: "", latency_ms: null },
  { key: "celery_worker", name: "Celery Worker", required: true, status: "unknown", detail: "", latency_ms: null },
  { key: "allure_cli", name: "Allure CLI", required: false, status: "unknown", detail: "", latency_ms: null },
];

function ServiceRow({
  svc,
  loading,
}: {
  svc: ServiceStatusItem;
  loading: boolean;
}) {
  const { Icon, color } = statusVisuals(svc.status);
  // title 属性兜底当 tooltip 用
  const tip = `${svc.name}\n${svc.detail || "无更多信息"}`;
  return (
    <div
      className="flex items-center gap-3 rounded-md border bg-card p-3"
      title={tip}
    >
      <Icon className={`h-5 w-5 shrink-0 ${color}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{svc.name}</span>
          {!svc.required && (
            <span className="inline-flex h-4 items-center rounded border px-1 text-[10px] text-muted-foreground">
              可选
            </span>
          )}
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {loading ? "检查中…" : svc.detail || "—"}
        </div>
      </div>
      {svc.latency_ms != null && (
        <span className="shrink-0 text-[11px] text-muted-foreground">
          {Math.round(svc.latency_ms)}ms
        </span>
      )}
    </div>
  );
}

function OverallBadge({ overall }: { overall: OverallHealth | "unknown" }) {
  const map: Record<string, { label: string; cls: string }> = {
    healthy: {
      label: "全部正常",
      cls: "border-emerald-200 bg-emerald-100 text-emerald-700",
    },
    degraded: {
      label: "降级运行",
      cls: "border-amber-200 bg-amber-100 text-amber-700",
    },
    down: {
      label: "异常",
      cls: "border-rose-200 bg-rose-100 text-rose-700",
    },
    unknown: {
      label: "检查中",
      cls: "border-border bg-muted text-muted-foreground",
    },
  };
  const v = map[overall] ?? map.unknown;
  return (
    <span
      className={`inline-flex h-5 items-center rounded border px-2 text-[11px] font-medium ${v.cls}`}
    >
      {v.label}
    </span>
  );
}

function statusVisuals(status: ServiceHealth) {
  if (status === "up") return { Icon: CheckCircle2, color: "text-emerald-500" };
  if (status === "down") return { Icon: XCircle, color: "text-rose-500" };
  return { Icon: HelpCircle, color: "text-muted-foreground" };
}

function formatTime(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString();
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// 入口快捷卡片
// ---------------------------------------------------------------------------
function QuickCard({
  to,
  title,
  desc,
  icon: Icon,
  disabled,
}: {
  to: string;
  title: string;
  desc: string;
  icon: React.ComponentType<{ className?: string }>;
  disabled?: boolean;
}) {
  const inner = (
    <Card
      className={
        disabled
          ? "opacity-60"
          : "transition-shadow hover:border-primary/60 hover:shadow-md"
      }
    >
      <CardHeader className="flex flex-row items-center gap-3 space-y-0 pb-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent">
          <Icon className="h-5 w-5" />
        </div>
        <CardTitle className="text-base">{title}</CardTitle>
        {!disabled && <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground" />}
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">{desc}</p>
      </CardContent>
    </Card>
  );
  return disabled ? inner : <Link to={to}>{inner}</Link>;
}
