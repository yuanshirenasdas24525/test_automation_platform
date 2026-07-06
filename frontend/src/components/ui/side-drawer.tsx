/**
 * 可复用的右侧抽屉（非模态、可拖拽调宽、记忆宽度）。
 *
 * - 非模态：无遮罩 / 无模糊 / 无阴影，左侧主列表依然可见、可滚动、可点击。
 * - 从右侧平滑滑出/收起（translate 过渡），关闭时移出视口且不拦截点击。
 * - 左边框可拖拽调宽：最小 minWidth，最大 = 当前视口宽度 * maxRatio；宽度写入 localStorage 记忆。
 * - 点击抽屉外的空白处可关闭（closeOnOutside，默认开）。
 * - 内容随宽度自适应（内部用 flex + 文字换行）。
 *
 * 用法：
 *   <SideDrawer open={open} onClose={...} storageKey="xxx-drawer" title={<>…</>} footer={<>…</>}>
 *     …内容…
 *   </SideDrawer>
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

export function SideDrawer({
  open,
  onClose,
  title,
  headerExtra,
  footer,
  children,
  storageKey,
  minWidth = 640,
  defaultWidth = 640,
  maxRatio = 0.75,
  closeOnOutside = true,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  headerExtra?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  /** localStorage key，用于记忆用户拖拽后的宽度。 */
  storageKey?: string;
  /** 拖拽最小宽度（px）。 */
  minWidth?: number;
  /** 初始宽度（无记忆时用）。 */
  defaultWidth?: number;
  /** 拖拽最大宽度 = 视口宽度 * maxRatio。 */
  maxRatio?: number;
  closeOnOutside?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);

  const maxWidth = useCallback(
    () => (typeof window !== "undefined" ? Math.round(window.innerWidth * maxRatio) : 1200),
    [maxRatio],
  );
  const clamp = useCallback(
    (w: number) => Math.max(minWidth, Math.min(w, maxWidth())),
    [minWidth, maxWidth],
  );

  const [width, setWidth] = useState<number>(() => {
    if (typeof window !== "undefined" && storageKey) {
      const saved = Number(window.localStorage.getItem(storageKey));
      if (saved && !Number.isNaN(saved)) return saved;
    }
    return defaultWidth;
  });

  // 记忆宽度 + 视口变化时重新收敛
  useEffect(() => {
    if (storageKey && typeof window !== "undefined") {
      window.localStorage.setItem(storageKey, String(width));
    }
  }, [width, storageKey]);

  useEffect(() => {
    const onResize = () => setWidth((w) => clamp(w));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  // 左边框拖拽调宽
  const onDragStart = (e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    const startX = e.clientX;
    const startW = width;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return;
      setWidth(clamp(startW + (startX - ev.clientX)));
    };
    const onUp = () => {
      draggingRef.current = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // 点击抽屉外的空白处关闭
  useEffect(() => {
    if (!open || !closeOnOutside) return;
    const onDown = (ev: MouseEvent) => {
      if (draggingRef.current) return;
      if (panelRef.current && !panelRef.current.contains(ev.target as Node)) {
        onClose();
      }
    };
    // 延后一帧再挂，避免"打开抽屉的那次点击"立刻把它关掉
    const t = window.setTimeout(() => document.addEventListener("mousedown", onDown), 0);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open, closeOnOutside, onClose]);

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-hidden={!open}
      className={cn(
        // 非模态：固定右侧、贴视口上下、仅 border-l 分隔，无阴影/遮罩。
        "fixed right-0 bottom-0 top-0 z-40 flex flex-col border-l bg-background transition-transform duration-200 ease-out",
        open ? "translate-x-0" : "pointer-events-none translate-x-full",
      )}
      style={{ width, maxWidth: "95vw" }}
    >
      {/* 左边框拖拽手柄 */}
      <div
        onMouseDown={onDragStart}
        title="拖拽调整宽度"
        className="absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-primary/30 active:bg-primary/40"
      />

      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2 truncate text-[15px] font-medium">{title}</div>
        <div className="flex shrink-0 items-center gap-3 text-muted-foreground">
          {headerExtra}
          <button title="关闭" onClick={onClose} className="hover:text-foreground">
            <X className="h-[17px] w-[17px]" />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>

      {footer ? <div className="border-t px-4 pb-5 pt-3">{footer}</div> : null}
    </div>
  );
}
