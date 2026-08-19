import { useCallback, useEffect, useRef, useState } from "react";

/**
 * 复制光效触发器：trigger(id) 给该 id 打上 flash 标记，durationMs 后自动清除。
 * flashing 集合驱动行上的 .copy-flash class。支持一次触发多个（多选复制）。
 */
export function useCopyFlash<T>(durationMs = 1200) {
  const [flashing, setFlashing] = useState<Set<T>>(new Set());
  const timers = useRef<Map<T, number>>(new Map());

  const trigger = useCallback(
    (ids: T | T[]) => {
      const arr = Array.isArray(ids) ? ids : [ids];
      setFlashing((prev) => {
        const next = new Set(prev);
        for (const id of arr) next.add(id);
        return next;
      });
      for (const id of arr) {
        const existing = timers.current.get(id);
        if (existing) window.clearTimeout(existing);
        const t = window.setTimeout(() => {
          setFlashing((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
          timers.current.delete(id);
        }, durationMs);
        timers.current.set(id, t);
      }
    },
    [durationMs],
  );

  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const t of map.values()) window.clearTimeout(t);
      map.clear();
    };
  }, []);

  return { flashing, trigger };
}
