/**
 * App 用例运行前的设备选择弹窗。
 *
 * 场景：用户在 ProjectDetailPage 点某条 app 用例 / 模块 / 项目的"运行"时，
 *   - 先弹出此对话框；
 *   - 默认 "自动（从池里挑）"，等价于不传 device_id —— 后端走 env.device_pool 过滤；
 *   - 用户也可以从 idle 设备列表里手选一台，此时把 device_id 传回，后端走
 *     `DevicePool.acquire_by_id` 锁定那一台，忽略 pool / platform 过滤。
 *
 * 非 app 场景（api / web）不应该弹这个对话框——调用方在 handleRunXxx 里自己判断。
 */
import { useQuery } from "@tanstack/react-query";
import { useState, useMemo, useEffect } from "react";
import { Smartphone, Loader2, Wifi, WifiOff, Clock } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { devicesApi, type Device } from "@/lib/api";
import { queryKeys } from "@/lib/query";

export interface DevicePickerDialogProps {
  open: boolean;
  /** 关闭对话框（取消运行）。 */
  onCancel: () => void;
  /** 确认运行：device_id 为 null 表示走自动池分配。 */
  onConfirm: (deviceId: number | null) => void;
  /** 提交中（运行接口还在飞）时禁用按钮，避免 double submit。 */
  submitting?: boolean;
  /** 标题里显示的目标名称，例如 "用例 login_ok"、"模块 登录流程"。 */
  target?: string;
}

export function DevicePickerDialog(props: DevicePickerDialogProps) {
  const { open, onCancel, onConfirm, submitting, target } = props;

  // 选中的 device_id：null 表示自动（从池里挑）。
  const [selected, setSelected] = useState<number | null>(null);

  // 每次打开对话框时重置选择——避免上一次选的某台设备后来离线了还被 retain
  useEffect(() => {
    if (open) setSelected(null);
  }, [open]);

  // 拉全量设备：前端自己过滤 idle/offline；offline 在 UI 里置灰不能选。
  // 用 query 打开后才启用，避免未打开就白跑请求。
  const devicesQuery = useQuery({
    queryKey: queryKeys.devices(),
    queryFn: () => devicesApi.list({}),
    enabled: open,
    refetchInterval: open ? 5000 : false,  // 开着的时候每 5s 刷一次，状态能跟上心跳探测
  });

  const devices = useMemo<Device[]>(() => devicesQuery.data ?? [], [devicesQuery.data]);

  const { idleList, nonIdleList } = useMemo(() => {
    const idle: Device[] = [];
    const other: Device[] = [];
    for (const d of devices) {
      if (d.status === "idle") idle.push(d);
      else other.push(d);
    }
    return { idleList: idle, nonIdleList: other };
  }, [devices]);

  const handleOpenChange = (v: boolean) => {
    if (!v) onCancel();
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>选择运行设备</DialogTitle>
          <DialogDescription>
            {target ? `目标：${target} · ` : ""}
            选择"自动"会让后端从对应设备池随机挑一台 idle 设备；
            手选某台则直接锁定那台，忽略池过滤。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
          {/* 自动（默认） */}
          <DeviceRow
            selected={selected === null}
            onClick={() => setSelected(null)}
            title="自动（从池里挑）"
            subtitle="让后端按 env.device_pool 随机选 idle 设备"
            indicator="auto"
          />

          {devicesQuery.isLoading ? (
            <div className="space-y-2 pt-1">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : devices.length === 0 ? (
            <p className="py-3 text-sm text-muted-foreground">
              还没注册任何设备。去 /devices 页面添加一台吧。
            </p>
          ) : (
            <>
              {idleList.length > 0 && (
                <div className="pt-2 text-xs font-semibold text-muted-foreground">
                  可用设备（idle）
                </div>
              )}
              {idleList.map((d) => (
                <DeviceRow
                  key={d.id}
                  selected={selected === d.id}
                  onClick={() => setSelected(d.id)}
                  title={d.device_name || d.udid}
                  subtitle={`${d.platform}${d.platform_version ? " " + d.platform_version : ""} · ${d.agent_host ?? "localhost"}:${d.appium_port ?? "-"} · pool=${d.pool}`}
                  indicator="idle"
                />
              ))}

              {nonIdleList.length > 0 && (
                <div className="pt-2 text-xs font-semibold text-muted-foreground">
                  不可选（busy / offline）
                </div>
              )}
              {nonIdleList.map((d) => (
                <DeviceRow
                  key={d.id}
                  selected={false}
                  disabled
                  onClick={() => {}}
                  title={d.device_name || d.udid}
                  subtitle={`${d.platform} · ${d.status}${d.consecutive_failures && d.consecutive_failures > 0 ? `（失败 ${d.consecutive_failures} 次）` : ""}`}
                  indicator={d.status === "offline" ? "offline" : "busy"}
                />
              ))}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          <Button
            onClick={() => onConfirm(selected)}
            disabled={submitting}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            开始运行
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 内部：一行设备卡片
// ---------------------------------------------------------------------------
function DeviceRow(props: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  subtitle: string;
  indicator: "auto" | "idle" | "busy" | "offline";
}) {
  const { selected, disabled, onClick, title, subtitle, indicator } = props;

  const Icon = indicator === "offline" ? WifiOff
    : indicator === "busy" ? Clock
    : indicator === "idle" ? Wifi
    : Smartphone;

  const iconCls = indicator === "offline"
    ? "text-muted-foreground"
    : indicator === "busy"
      ? "text-amber-600"
      : indicator === "idle"
        ? "text-emerald-600"
        : "text-primary";

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 rounded border px-3 py-2 text-left transition",
        selected ? "border-primary bg-primary/5" : "border-input",
        disabled ? "cursor-not-allowed opacity-60" : "hover:bg-accent",
      )}
    >
      <Icon className={cn("h-4 w-4 shrink-0", iconCls)} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{title}</div>
        <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
      </div>
      {selected ? (
        <span className="shrink-0 rounded bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground">
          已选
        </span>
      ) : null}
    </button>
  );
}
