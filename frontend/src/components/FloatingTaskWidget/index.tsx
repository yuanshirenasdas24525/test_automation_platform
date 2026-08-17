/**
 * 全局浮动任务列表。
 *
 * 行为：
 *   - 右下角白底黑字圆形，显示进行中任务数
 *   - 可拖拽，松手吸附最近边缘
 *   - 点击圆形从侧边滑出任务面板；鼠标移出面板收回
 *   - 有任务时显示，无任务时隐藏
 *   - 每 2s 轮询 /api/tasks-overview/in-progress
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Brain,
  Bug,
  ClipboardList,
  FileBarChart,
  FileText,
  Gauge,
  Globe,
  Loader2,
  Monitor,
  Play,
  SearchCheck,
  Smartphone,
  Sparkles,
  StopCircle,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";

import { tasksOverviewApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import { cn } from "@/lib/utils";
import type { InProgressTask } from "@/types/domain";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------
const CIRCLE = 48;
const GAP = 12;
const DRAG_THRESHOLD = 5;
const LOCAL_TASKS_KEY = "local-in-progress-tasks:v1";
const LOCAL_TASKS_EVENT = "local-in-progress-tasks-change";
const LOCAL_TASKS_CANCEL_EVENT = "local-in-progress-task-cancel";

// ---------------------------------------------------------------------------
// 图标映射
// ---------------------------------------------------------------------------
const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  Brain,
  Bug,
  Sparkles,
  Globe,
  Monitor,
  Smartphone,
  FileText,
  ClipboardList,
  SearchCheck,
  Workflow,
  FileBarChart,
  Gauge,
  Play,
};

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------
function defaultX(): number {
  return window.innerWidth - CIRCLE - GAP;
}
function defaultY(): number {
  return window.innerHeight - 160;
}
function formatElapsed(iso: string | null): string | null {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return null;
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function readLocalTasks(): InProgressTask[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(LOCAL_TASKS_KEY);
    if (!raw) return [];
    const items = JSON.parse(raw) as InProgressTask[];
    const staleAt = Date.now() - 4 * 60 * 60 * 1000;
    return (Array.isArray(items) ? items : []).filter((item) => {
      if (item.status !== "running" && item.status !== "pending") return false;
      if (!item.started_at) return true;
      return new Date(item.started_at).getTime() >= staleAt;
    });
  } catch {
    return [];
  }
}

function removeLocalTask(typeKey: string, id: number) {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(LOCAL_TASKS_KEY);
    const items = raw ? JSON.parse(raw) as InProgressTask[] : [];
    const next = (Array.isArray(items) ? items : []).filter(
      (item) => item.type_key !== typeKey && item.id !== id,
    );
    window.localStorage.setItem(LOCAL_TASKS_KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(LOCAL_TASKS_EVENT));
  } catch {
    // 本地任务只是 UI 辅助，失败不影响主流程。
  }
}

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------
export function FloatingTaskWidget() {
  const queryClient = useQueryClient();
  const [pos, setPos] = useState({ x: defaultX(), y: defaultY() });
  const [expanded, setExpanded] = useState(false);
  const [localTasks, setLocalTasks] = useState<InProgressTask[]>(() => readLocalTasks());
  const dragging = useRef(false);
  const moved = useRef(false);
  const offset = useRef({ x: 0, y: 0 });
  const start = useRef({ x: 0, y: 0 });
  const elRef = useRef<HTMLDivElement>(null);

  const { data: tasks = [] } = useQuery<InProgressTask[]>({
    queryKey: queryKeys.tasksInProgress(),
    queryFn: () => tasksOverviewApi.getInProgress(),
    refetchInterval: 2_000,
    staleTime: 1_500,
  });

  const cancelMutation = useMutation({
    mutationFn: (task: InProgressTask) => tasksOverviewApi.cancelTask(task.type_key, task.id),
    onSuccess: (res) => {
      toast.success(res.message || "已终止任务");
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasksInProgress() });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : "终止任务失败";
      toast.error(message);
    },
  });

  useEffect(() => {
    const reload = () => setLocalTasks(readLocalTasks());
    const timer = window.setInterval(reload, 2_000);
    window.addEventListener(LOCAL_TASKS_EVENT, reload);
    window.addEventListener("storage", reload);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(LOCAL_TASKS_EVENT, reload);
      window.removeEventListener("storage", reload);
    };
  }, []);

  const mergedTasks = [...localTasks, ...tasks];
  const count = mergedTasks.length;
  const runningCount = mergedTasks.filter((t) => t.status === "running").length;
  const visible = count > 0;

  const circleOnLeft = pos.x + CIRCLE / 2 < window.innerWidth / 2;

  // ── 吸附 ─────────────────────────────────────────────────────────
  const snap = useCallback(
    (x: number, y: number) => {
      const cy = Math.max(GAP, Math.min(y, window.innerHeight - CIRCLE - GAP));
      const isLeft = x + CIRCLE / 2 < window.innerWidth / 2;
      return { x: isLeft ? GAP : window.innerWidth - CIRCLE - GAP, y: cy };
    },
    [],
  );

  // ── 拖拽事件 ─────────────────────────────────────────────────────
  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-task-drag-handle]")) return;
      e.preventDefault();
      dragging.current = false;
      moved.current = false;
      start.current = { x: e.clientX, y: e.clientY };
      offset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
      elRef.current?.setPointerCapture(e.pointerId);
    },
    [pos],
  );

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!elRef.current?.hasPointerCapture(e.pointerId)) return;
    const dx = Math.abs(e.clientX - start.current.x);
    const dy = Math.abs(e.clientY - start.current.y);
    if (!dragging.current && (dx > DRAG_THRESHOLD || dy > DRAG_THRESHOLD)) {
      dragging.current = true;
      moved.current = true;
    }
    if (dragging.current) {
      setPos({
        x: e.clientX - offset.current.x,
        y: e.clientY - offset.current.y,
      });
    }
  }, []);

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      if (!elRef.current?.hasPointerCapture(e.pointerId)) return;
      elRef.current?.releasePointerCapture(e.pointerId);
      if (moved.current) {
        setPos((p) => snap(p.x, p.y));
      }
      if (!dragging.current && visible) {
        setExpanded((v) => !v);
      }
      dragging.current = false;
      moved.current = false;
    },
    [snap, visible],
  );

  // ── 窗口 resize 时重新吸附 ───────────────────────────────────────
  useEffect(() => {
    const onResize = () => setPos((p) => snap(p.x, p.y));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [snap]);

  // ── 鼠标移出整个 widget 区域时收回面板 ──────────────────────────
  const onMouseLeave = useCallback(() => setExpanded(false), []);

  const cancelTask = useCallback((task: InProgressTask) => {
    if (task.type_key.startsWith("local_")) {
      removeLocalTask(task.type_key, task.id);
      window.dispatchEvent(new CustomEvent(LOCAL_TASKS_CANCEL_EVENT, {
        detail: { type_key: task.type_key, id: task.id },
      }));
      setLocalTasks(readLocalTasks());
      toast.success("已终止任务");
      return;
    }
    cancelMutation.mutate(task);
  }, [cancelMutation]);

  return (
    <div
      ref={elRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onMouseLeave={onMouseLeave}
      className="fixed z-[9999] select-none touch-none"
      style={{
        left: pos.x,
        top: pos.y,
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? "auto" : "none",
        transition: "opacity 0.3s ease",
      }}
    >
      {/* ── 弹出面板 ──────────────────────────────────────────── */}
      <div
        data-task-drag-handle
        className={cn(
          "absolute top-0 w-72 rounded-xl border bg-white shadow-xl transition-all duration-300 ease-out",
          expanded
            ? "opacity-100 scale-100"
            : "pointer-events-none scale-95 opacity-0",
        )}
        style={circleOnLeft ? {
          left: CIRCLE + GAP,
          maxHeight: "60vh",
        } : {
          right: CIRCLE + GAP,
          maxHeight: "60vh",
        }}
      >
        {/* 小三角 */}
        <div
          className="absolute top-5 h-0 w-0 border-y-8 border-transparent"
          style={
            circleOnLeft
              ? { left: -8, borderRight: "8px solid white" }
              : { right: -8, borderLeft: "8px solid white" }
          }
        />

        <div className="flex items-center justify-between border-b px-4 py-3">
          <span className="text-sm font-semibold text-gray-800">
            进行中 · {count}
          </span>
          <button
            onClick={() => setExpanded(false)}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3l8 8M11 3l-8 8" />
            </svg>
          </button>
        </div>

        <div className="overflow-y-auto" style={{ maxHeight: "calc(60vh - 48px)" }}>
          {mergedTasks.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">
              当前没有进行中的任务
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {mergedTasks.map((t) => {
                const Icon = ICONS[t.icon] ?? Loader2;
                const elapsed = formatElapsed(t.started_at);
                return (
                  <div
                    key={`${t.type_key}-${t.id}`}
                    className="flex items-start gap-2 px-4 py-3 text-sm hover:bg-gray-50 transition-colors"
                  >
                    <Link
                      to={t.detail_url || "#"}
                      onClick={() => setExpanded(false)}
                      className="flex min-w-0 flex-1 items-start gap-3"
                    >
                      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-gray-500" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium text-gray-800">{t.name}</div>
                        <div className="mt-0.5 flex items-center gap-2">
                          {t.status === "running" ? (
                            <span className="inline-flex items-center gap-1 text-xs text-blue-600">
                              <Loader2 className="h-3 w-3 animate-spin" />
                              运行中
                            </span>
                          ) : t.status === "pending" ? (
                            <span className="inline-flex items-center gap-1 text-xs text-amber-600">
                              <Loader2 className="h-3 w-3" />
                              等待中
                            </span>
                          ) : (
                            <span className="text-xs text-gray-400">{t.status}</span>
                          )}
                          <span className="text-xs text-gray-400">{t.type_label}</span>
                        </div>
                        {t.project_name ? (
                          <div className="mt-0.5 truncate text-xs text-gray-400">
                            {t.project_name}
                          </div>
                        ) : null}
                      </div>
                    </Link>
                    <div className="flex shrink-0 items-start gap-1">
                      {elapsed ? (
                        <span className="pt-0.5 text-xs text-gray-400">{elapsed}</span>
                      ) : null}
                      <button
                        type="button"
                        title="终止任务"
                        aria-label="终止任务"
                        disabled={cancelMutation.isPending}
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          cancelTask(t);
                        }}
                        className="rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <StopCircle className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── 浮动圆形按钮 ──────────────────────────────────────── */}
      <div
        className={cn(
          "flex h-[48px] w-[48px] items-center justify-center rounded-full border-2 border-gray-300 bg-white shadow-lg transition-shadow hover:shadow-xl",
          expanded && "ring-2 ring-blue-200",
        )}
      >
        {runningCount > 0 ? (
          <svg
            className="absolute h-[48px] w-[48px] animate-spin"
            viewBox="0 0 48 48"
          >
            <circle
              cx="24" cy="24" r="20"
              fill="none"
              stroke="#3b82f6"
              strokeWidth="2"
              strokeDasharray="30 100"
              strokeLinecap="round"
              className="opacity-30"
            />
          </svg>
        ) : null}
        <span className="z-10 text-lg font-bold text-gray-800">{count}</span>
        {runningCount > 0 ? (
          <span className="absolute -right-1 -top-1 flex h-[18px] w-[18px] items-center justify-center rounded-full bg-white border border-gray-300 shadow-sm">
            <Loader2 className="h-[10px] w-[10px] animate-spin text-blue-500" />
          </span>
        ) : null}
      </div>
    </div>
  );
}
