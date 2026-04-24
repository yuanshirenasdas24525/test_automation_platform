import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Smartphone,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  ApiError,
  devicesApi,
  type Device,
  type DeviceStatus,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";

/**
 * 设备池页面：用于给 App 自动化注册 / 管理真机 & 模拟器。
 *
 * 核心数据流：
 *   - 后端 devices 表 <-- 本页面的 CRUD
 *   - 用例运行到 app_* step 时，CaseExecutor 从这张表里按 pool 抢一台 idle 设备
 *   - 本页面不提供 acquire —— 那是跑用例时才触发；但提供 release（手动解锁异常 busy）
 */

const PLATFORM_OPTIONS = ["Android", "iOS"] as const;
const STATUS_OPTIONS: { value: DeviceStatus; label: string; color: string }[] = [
  { value: "idle", label: "空闲", color: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  { value: "busy", label: "占用中", color: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  { value: "offline", label: "离线", color: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
];

const deviceSchema = z.object({
  udid: z.string().trim().min(1, "udid 必填").max(128),
  platform: z.enum(["Android", "iOS"]),
  platform_version: z.string().trim().optional().or(z.literal("")),
  device_name: z.string().trim().optional().or(z.literal("")),
  brand: z.string().trim().optional().or(z.literal("")),
  model: z.string().trim().optional().or(z.literal("")),
  agent_host: z.string().trim().optional().or(z.literal("")),
  agent_port: z
    .union([z.coerce.number().int().positive().max(65535), z.literal("")])
    .optional(),
  appium_port: z
    .union([z.coerce.number().int().positive().max(65535), z.literal("")])
    .optional(),
  pool: z.string().trim().max(64).optional().or(z.literal("")),
  status: z.enum(["idle", "offline"]).optional(),
  tags: z.string().optional(), // 逗号分隔
  capabilities: z.string().optional(), // JSON 文本
});
type DeviceFormValues = z.infer<typeof deviceSchema>;

// 默认值：新增时用；编辑时从 device 里构造
const EMPTY_FORM: DeviceFormValues = {
  udid: "",
  platform: "Android",
  platform_version: "",
  device_name: "",
  brand: "",
  model: "",
  agent_host: "",
  agent_port: "" as unknown as number,
  appium_port: "" as unknown as number,
  pool: "default",
  status: "offline",
  tags: "",
  capabilities: "",
};

// ============================================================
// 主组件
// ============================================================
export function DevicesPage() {
  const queryClient = useQueryClient();

  const [poolFilter, setPoolFilter] = useState<string>("");
  const [platformFilter, setPlatformFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  const activeFilters = useMemo(
    () => ({
      pool: poolFilter || undefined,
      platform: platformFilter || undefined,
      status: (statusFilter || undefined) as DeviceStatus | undefined,
    }),
    [poolFilter, platformFilter, statusFilter],
  );

  const devicesQuery = useQuery({
    queryKey: queryKeys.devices({
      pool: poolFilter,
      platform: platformFilter,
      status: statusFilter,
    }),
    queryFn: () => devicesApi.list(activeFilters),
    // 后端 Celery beat 每 30s 心跳探测一次；前端 15s 拉一次基本能做到用户"感知及时"。
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });

  const poolsQuery = useQuery({
    queryKey: queryKeys.devicePools(),
    queryFn: () => devicesApi.pools(),
    staleTime: 60 * 1000,
  });

  const [editing, setEditing] = useState<Device | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Device | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["devices"] });
    queryClient.invalidateQueries({ queryKey: queryKeys.devicePools() });
  };

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const createMutation = useMutation({
    mutationFn: (body: DeviceFormValues) => devicesApi.create(formToBody(body)),
    onSuccess: () => {
      toast.success("已注册");
      invalidate();
      setCreating(false);
    },
    onError: handleError,
  });

  const updateMutation = useMutation({
    mutationFn: (args: { id: number; body: DeviceFormValues }) =>
      devicesApi.update(args.id, formToBody(args.body)),
    onSuccess: () => {
      toast.success("已更新");
      invalidate();
      setEditing(null);
    },
    onError: handleError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => devicesApi.remove(id),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const releaseMutation = useMutation({
    mutationFn: (id: number) => devicesApi.release(id),
    onSuccess: () => {
      toast.success("已释放");
      invalidate();
    },
    onError: handleError,
  });

  const devices = devicesQuery.data ?? [];
  const pools = poolsQuery.data ?? ["default"];

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">设备池</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            管理 App 自动化用的真机和模拟器。跑 app_* step 时，CaseExecutor 会按环境里的{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs">device_pool</code>{" "}
            从这里抢一台 idle 设备，用完自动 release。
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => devicesQuery.refetch()}
            disabled={devicesQuery.isFetching}
          >
            {devicesQuery.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            刷新
          </Button>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            注册设备
          </Button>
        </div>
      </div>

      {/* 过滤栏 */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">设备池</Label>
          <Select
            value={poolFilter || "__all__"}
            onValueChange={(v) => setPoolFilter(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="h-8 w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              {pools.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">平台</Label>
          <Select
            value={platformFilter || "__all__"}
            onValueChange={(v) =>
              setPlatformFilter(v === "__all__" ? "" : v)
            }
          >
            <SelectTrigger className="h-8 w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              {PLATFORM_OPTIONS.map((p) => (
                <SelectItem key={p} value={p}>
                  {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">状态</Label>
          <Select
            value={statusFilter || "__all__"}
            onValueChange={(v) => setStatusFilter(v === "__all__" ? "" : v)}
          >
            <SelectTrigger className="h-8 w-[140px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              {STATUS_OPTIONS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {devicesQuery.isLoading ? (
        <ListSkeleton />
      ) : devicesQuery.isError ? (
        <ErrorBox
          message={
            devicesQuery.error instanceof Error
              ? devicesQuery.error.message
              : "加载失败"
          }
          onRetry={() => devicesQuery.refetch()}
        />
      ) : devices.length === 0 ? (
        <EmptyState onCreate={() => setCreating(true)} />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {devices.map((d) => (
            <DeviceCard
              key={d.id}
              device={d}
              onEdit={() => setEditing(d)}
              onDelete={() => setPendingDelete(d)}
              onRelease={() => releaseMutation.mutate(d.id)}
              releasing={
                releaseMutation.isPending &&
                releaseMutation.variables === d.id
              }
            />
          ))}
        </div>
      )}

      {/* 新增 / 编辑对话框 */}
      <DeviceFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        title="注册设备"
        submitting={createMutation.isPending}
        defaultValues={EMPTY_FORM}
        onSubmit={(v) => createMutation.mutate(v)}
      />
      <DeviceFormDialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        title="编辑设备"
        submitting={updateMutation.isPending}
        defaultValues={editing ? deviceToForm(editing) : EMPTY_FORM}
        onSubmit={(v) =>
          editing && updateMutation.mutate({ id: editing.id, body: v })
        }
        lockUdid
      />

      {/* 删除确认 */}
      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除设备</DialogTitle>
            <DialogDescription>
              确认删除 「{pendingDelete?.udid}」？
              {pendingDelete?.status === "busy"
                ? " 该设备当前 busy，后端会拒绝删除，请先 release。"
                : " 此操作不可恢复。"}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingDelete(null)}
              disabled={deleteMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() =>
                pendingDelete && deleteMutation.mutate(pendingDelete.id)
              }
            >
              {deleteMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ============================================================
// 单个设备卡片
// ============================================================
function DeviceCard({
  device,
  onEdit,
  onDelete,
  onRelease,
  releasing,
}: {
  device: Device;
  onEdit: () => void;
  onDelete: () => void;
  onRelease: () => void;
  releasing: boolean;
}) {
  const statusMeta =
    STATUS_OPTIONS.find((s) => s.value === device.status) ?? STATUS_OPTIONS[2];
  const appiumUrl =
    device.agent_host && device.appium_port
      ? `http://${device.agent_host}:${device.appium_port}/wd/hub`
      : null;

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Smartphone className="h-4 w-4 text-muted-foreground" />
              <span className="truncate font-mono text-xs font-semibold">
                {device.udid}
              </span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="rounded bg-muted px-1.5 py-0.5">
                {device.platform}
                {device.platform_version ? ` ${device.platform_version}` : ""}
              </span>
              <span className="rounded bg-muted px-1.5 py-0.5">
                pool: {device.pool}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 font-medium ${statusMeta.color}`}
                title={
                  device.owner_execution_id
                    ? `占用者 execution_id=${device.owner_execution_id}`
                    : undefined
                }
              >
                {statusMeta.label}
              </span>
            </div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-8 w-8">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  onEdit();
                }}
              >
                <Pencil className="h-4 w-4" />
                编辑
              </DropdownMenuItem>
              {device.status === "busy" ? (
                <DropdownMenuItem
                  onSelect={(e) => {
                    e.preventDefault();
                    onRelease();
                  }}
                  disabled={releasing}
                >
                  <RotateCcw className="h-4 w-4" />
                  强制 release
                </DropdownMenuItem>
              ) : null}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onSelect={(e) => {
                  e.preventDefault();
                  onDelete();
                }}
              >
                <Trash2 className="h-4 w-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="space-y-1 text-xs">
          {device.brand || device.model ? (
            <Row label="型号">
              {[device.brand, device.model].filter(Boolean).join(" ")}
            </Row>
          ) : null}
          {appiumUrl ? (
            <Row label="Appium">
              <code className="font-mono text-[11px]">{appiumUrl}</code>
            </Row>
          ) : (
            <Row label="Appium">
              <span className="italic text-muted-foreground">
                未填 agent_host / appium_port
              </span>
            </Row>
          )}
          {device.tags && device.tags.length > 0 ? (
            <Row label="标签">
              <div className="flex flex-wrap gap-1">
                {device.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded bg-muted px-1.5 py-0.5 text-[10px]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </Row>
          ) : null}
          <Row label="心跳">
            <HeartbeatInline device={device} />
          </Row>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * 把 last_heartbeat 和 consecutive_failures 融合成一行"活着没"的可读说明。
 * 规则：
 *   - 从来没探测成功过 → "未探测（刚注册？）"
 *   - 10 分钟内探测成功 → 绿点 + "x秒/分钟前"
 *   - 超 10 分钟 → 灰点 + 绝对时间
 *   - consecutive_failures > 0 时附加"近 N 次失败"
 */
function HeartbeatInline({ device }: { device: Device }) {
  const failures = device.consecutive_failures ?? 0;

  if (!device.last_heartbeat) {
    return (
      <span className="text-muted-foreground">
        <span className="mr-1 inline-block h-2 w-2 rounded-full bg-zinc-300 align-middle" />
        {failures > 0 ? `未成功探测过（失败 ${failures} 次）` : "未探测"}
      </span>
    );
  }

  const ts = Date.parse(device.last_heartbeat);
  const deltaMs = Number.isFinite(ts) ? Date.now() - ts : Number.POSITIVE_INFINITY;
  const fresh = deltaMs >= 0 && deltaMs < 10 * 60 * 1000;

  return (
    <span className={fresh ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground"}>
      <span
        className={
          "mr-1 inline-block h-2 w-2 rounded-full align-middle " +
          (fresh ? "bg-emerald-500 animate-pulse" : "bg-zinc-400")
        }
      />
      {fresh ? humanizeAgo(deltaMs) : new Date(ts).toLocaleString()}
      {failures > 0 ? (
        <span className="ml-2 text-xs text-amber-700 dark:text-amber-400">
          （连失败 {failures}）
        </span>
      ) : null}
    </span>
  );
}

function humanizeAgo(ms: number): string {
  const sec = Math.max(0, Math.floor(ms / 1000));
  if (sec < 5) return "刚刚";
  if (sec < 60) return `${sec} 秒前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  return `${hr} 小时前`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="w-16 shrink-0 text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1 break-all">{children}</div>
    </div>
  );
}

// ============================================================
// 注册 / 编辑对话框
// ============================================================
function DeviceFormDialog({
  open,
  onClose,
  title,
  defaultValues,
  submitting,
  onSubmit,
  lockUdid = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  defaultValues: DeviceFormValues;
  submitting: boolean;
  onSubmit: (v: DeviceFormValues) => void;
  lockUdid?: boolean;
}) {
  const form = useForm<DeviceFormValues>({
    resolver: zodResolver(deviceSchema),
    defaultValues,
    values: defaultValues,
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            udid 和平台是必填；其它字段缺省会用 default / 空值。appium_port
            是该设备专属的 Appium Server 端口（不是 Selenium Grid）。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={form.handleSubmit((v) => onSubmit(v))}
        >
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dev-udid">udid *</Label>
              <Input
                id="dev-udid"
                disabled={lockUdid}
                placeholder="emulator-5554 或真机 SN"
                {...form.register("udid")}
              />
              {form.formState.errors.udid ? (
                <p className="text-xs text-destructive">
                  {form.formState.errors.udid.message}
                </p>
              ) : null}
            </div>
            <div className="space-y-1.5">
              <Label>平台 *</Label>
              <Select
                value={form.watch("platform")}
                onValueChange={(v) =>
                  form.setValue("platform", v as "Android" | "iOS", {
                    shouldDirty: true,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLATFORM_OPTIONS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dev-platform-version">系统版本</Label>
              <Input
                id="dev-platform-version"
                placeholder="例 13.0"
                {...form.register("platform_version")}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dev-device-name">设备名</Label>
              <Input
                id="dev-device-name"
                placeholder="例 Pixel 6"
                {...form.register("device_name")}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dev-brand">品牌</Label>
              <Input id="dev-brand" {...form.register("brand")} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dev-model">机型</Label>
              <Input id="dev-model" {...form.register("model")} />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5 col-span-3 md:col-span-1">
              <Label htmlFor="dev-agent-host">agent_host</Label>
              <Input
                id="dev-agent-host"
                placeholder="localhost"
                {...form.register("agent_host")}
              />
            </div>
            <div className="space-y-1.5 col-span-3 md:col-span-1">
              <Label htmlFor="dev-agent-port">agent_port</Label>
              <Input
                id="dev-agent-port"
                type="number"
                placeholder="可空"
                {...form.register("agent_port")}
              />
            </div>
            <div className="space-y-1.5 col-span-3 md:col-span-1">
              <Label htmlFor="dev-appium-port">appium_port</Label>
              <Input
                id="dev-appium-port"
                type="number"
                placeholder="4723"
                {...form.register("appium_port")}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dev-pool">设备池</Label>
              <Input
                id="dev-pool"
                placeholder="default"
                {...form.register("pool")}
              />
            </div>
            <div className="space-y-1.5">
              <Label>初始状态</Label>
              <Select
                value={form.watch("status") ?? "offline"}
                onValueChange={(v) =>
                  form.setValue("status", v as "idle" | "offline", {
                    shouldDirty: true,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="offline">offline（未连）</SelectItem>
                  <SelectItem value="idle">idle（可被调度）</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dev-tags">标签</Label>
            <Input
              id="dev-tags"
              placeholder="逗号分隔，例：高版本,4G卡"
              {...form.register("tags")}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dev-capabilities">额外 Capabilities（JSON）</Label>
            <Textarea
              id="dev-capabilities"
              rows={4}
              placeholder='例：{"appPackage":"com.example","appActivity":".MainActivity"}'
              {...form.register("capabilities")}
            />
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================
// 表单 <-> DTO 转换
// ============================================================
function formToBody(v: DeviceFormValues) {
  const toNum = (x: number | string | undefined | null): number | undefined => {
    if (x === undefined || x === null || x === "") return undefined;
    const n = typeof x === "number" ? x : Number(x);
    return Number.isFinite(n) ? n : undefined;
  };

  let caps: Record<string, unknown> | undefined;
  const capStr = (v.capabilities || "").trim();
  if (capStr) {
    try {
      const parsed = JSON.parse(capStr);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        caps = parsed as Record<string, unknown>;
      } else {
        throw new Error("capabilities 必须是 JSON 对象");
      }
    } catch (e) {
      throw new ApiError(
        `capabilities JSON 解析失败: ${(e as Error).message}`,
        400,
      );
    }
  }

  const tags = (v.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

  return {
    udid: v.udid.trim(),
    platform: v.platform,
    platform_version: v.platform_version?.trim() || undefined,
    device_name: v.device_name?.trim() || undefined,
    brand: v.brand?.trim() || undefined,
    model: v.model?.trim() || undefined,
    agent_host: v.agent_host?.trim() || undefined,
    agent_port: toNum(v.agent_port),
    appium_port: toNum(v.appium_port),
    pool: v.pool?.trim() || "default",
    status: v.status,
    tags: tags.length > 0 ? tags : undefined,
    capabilities: caps,
  };
}

function deviceToForm(d: Device): DeviceFormValues {
  return {
    udid: d.udid,
    platform: d.platform,
    platform_version: d.platform_version ?? "",
    device_name: d.device_name ?? "",
    brand: d.brand ?? "",
    model: d.model ?? "",
    agent_host: d.agent_host ?? "",
    agent_port: (d.agent_port ?? "") as unknown as number,
    appium_port: (d.appium_port ?? "") as unknown as number,
    pool: d.pool,
    // 编辑模式下不让改成 busy；busy 态保留但下拉里不展示
    status: d.status === "busy" ? "idle" : (d.status as "idle" | "offline"),
    tags: (d.tags ?? []).join(", "),
    capabilities: d.capabilities ? JSON.stringify(d.capabilities, null, 2) : "",
  };
}

// ============================================================
// 空态 / 骨架 / 错误
// ============================================================
function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <Smartphone className="h-8 w-8 text-muted-foreground" />
        <div className="text-sm text-muted-foreground">
          还没有注册任何设备。注册一台之后就能用 App 自动化用例了。
        </div>
        <Button variant="outline" onClick={onCreate}>
          <Plus className="h-4 w-4" />
          注册第一台
        </Button>
      </CardContent>
    </Card>
  );
}

function ListSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardContent className="space-y-2 p-4">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-3 w-2/3" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/50">
      <CardContent className="flex flex-col items-start gap-3 py-6">
        <div className="text-sm text-destructive">加载失败：{message}</div>
        <Button onClick={onRetry} variant="outline" size="sm">
          重试
        </Button>
      </CardContent>
    </Card>
  );
}
