import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Loader2, ListTree } from "lucide-react";

import { uiRecordingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UiElement, UiPlatform } from "@/types/domain";

type Bounds = { x: number; y: number; w: number; h: number };
type TreeNode = {
  key: string;
  tag: string;
  attrs: Record<string, string>;
  bounds: Bounds | null;
  children: TreeNode[];
};

/**
 * Android bounds: "[x1,y1][x2,y2]"（像素）；iOS: x/y/width/height（点 pt）。
 * scale：把 iOS 的"点"换算到"像素"，与元素库存的 bounds（已缩放为像素）对齐，
 * 否则 Retina（2/3 倍）下树节点匹配不到元素、点了没反应。Android 恒为 1。
 */
function nodeBounds(attrs: Record<string, string>, scale = 1): Bounds | null {
  const b = attrs["bounds"];
  if (b) {
    const m = b.match(/\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]/);
    if (m) {
      const x = +m[1], y = +m[2], x2 = +m[3], y2 = +m[4];
      return { x, y, w: x2 - x, h: y2 - y };
    }
  }
  if (attrs["x"] != null && attrs["width"] != null) {
    const s = scale || 1;
    return {
      x: Math.round(+attrs["x"] * s),
      y: Math.round(+attrs["y"] * s),
      w: Math.round(+attrs["width"] * s),
      h: Math.round(+attrs["height"] * s),
    };
  }
  return null;
}

function shortTag(tag: string): string {
  return tag.split(".").pop() || tag;
}

/** 节点标签摘要：content-desc / text / resource-id，供一眼识别。 */
function nodeLabel(attrs: Record<string, string>): { kind: string; value: string } | null {
  const acc = attrs["content-desc"] || attrs["name"] || attrs["label"];
  if (acc) return { kind: "desc", value: acc };
  const text = (attrs["text"] || attrs["value"] || "").trim();
  if (text) return { kind: "text", value: text };
  const rid = attrs["resource-id"] || attrs["resourceId"];
  if (rid) return { kind: "id", value: rid.split("/").pop() || rid };
  return null;
}

function buildTree(xml: string, scale = 1): TreeNode | null {
  let doc: Document;
  try {
    doc = new DOMParser().parseFromString(xml, "application/xml");
  } catch {
    return null;
  }
  if (doc.querySelector("parsererror")) return null;
  let seq = 0;
  const walk = (el: Element): TreeNode => {
    const attrs: Record<string, string> = {};
    for (const a of Array.from(el.attributes)) attrs[a.name] = a.value;
    return {
      key: `n${seq++}`,
      tag: el.tagName,
      attrs,
      bounds: nodeBounds(attrs, scale),
      children: Array.from(el.children).map(walk),
    };
  };
  const root = doc.documentElement;
  return root ? walk(root) : null;
}

export function UiTreePanel({
  snapshotId,
  platform,
  elements,
  selectedElementId,
  onSelectElement,
}: {
  snapshotId: number | null;
  platform: UiPlatform;
  elements: UiElement[];
  selectedElementId: number | null;
  onSelectElement: (id: number) => void;
}) {
  const docQuery = useQuery({
    queryKey: ["snapshot-document", snapshotId],
    queryFn: () => uiRecordingsApi.snapshotDocument(snapshotId as number),
    enabled: snapshotId != null && platform !== "web",
    staleTime: 60_000,
  });
  // iOS 的元素库 bounds 已被录制器缩放为像素，但 attributes.width 仍是原始点值 ——
  // 反推缩放比例，让树里解析出的点坐标同样换算到像素、和元素库对齐。Android 恒为 1。
  const scale = useMemo(() => {
    for (const el of elements) {
      const b = el.attributes?.bounds as { width?: number } | undefined;
      const rawW = Number(el.attributes?.width);
      if (b?.width && rawW > 0) {
        const r = b.width / rawW;
        if (r > 1.05) return r;
      }
    }
    return 1;
  }, [elements]);
  const tree = useMemo(
    () => (docQuery.data ? buildTree(docQuery.data, scale) : null),
    [docQuery.data, scale],
  );

  // 按 bounds 把节点映射到元素库元素（点树选中、能看定位器）
  const elementByBounds = useMemo(() => {
    const map = new Map<string, UiElement>();
    for (const el of elements) {
      const b = el.attributes?.bounds as { x: number; y: number; width: number; height: number } | undefined;
      if (b && b.width) map.set(`${b.x},${b.y},${b.width},${b.height}`, el);
    }
    return map;
  }, [elements]);

  const elementFor = (node: TreeNode): UiElement | null => {
    if (!node.bounds) return null;
    return elementByBounds.get(`${node.bounds.x},${node.bounds.y},${node.bounds.w},${node.bounds.h}`) ?? null;
  };

  // 选中元素 → 它对应的节点 key（用于树里高亮 + 自动展开）
  const selectedBounds = useMemo(() => {
    const el = elements.find((e) => e.id === selectedElementId);
    const b = el?.attributes?.bounds as { x: number; y: number; width: number; height: number } | undefined;
    return b ? { x: b.x, y: b.y, w: b.width, h: b.height } : null;
  }, [elements, selectedElementId]);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [collapsedInit, setCollapsedInit] = useState<string | null>(null);
  // 首次拿到树：默认展开前几层，方便浏览
  useEffect(() => {
    if (!tree || collapsedInit === docQuery.data) return;
    const next = new Set<string>();
    const seed = (n: TreeNode, depth: number) => {
      if (depth < 6) next.add(n.key);
      n.children.forEach((c) => seed(c, depth + 1));
    };
    seed(tree, 0);
    setExpanded(next);
    setCollapsedInit(docQuery.data ?? null);
  }, [tree, docQuery.data, collapsedInit]);

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });

  const renderNode = (node: TreeNode, depth: number) => {
    const el = elementFor(node);
    const isSelected = selectedBounds != null && node.bounds != null
      && node.bounds.x === selectedBounds.x && node.bounds.y === selectedBounds.y
      && node.bounds.w === selectedBounds.w && node.bounds.h === selectedBounds.h;
    const label = nodeLabel(node.attrs);
    const hasChildren = node.children.length > 0;
    const open = expanded.has(node.key);
    return (
      <div key={node.key}>
        <div
          className={cn(
            "flex items-center gap-1 rounded px-1 py-0.5 text-[11px] leading-5",
            isSelected ? "bg-primary/15 text-primary ring-1 ring-primary/40" : "hover:bg-muted",
            el ? "cursor-pointer" : "cursor-default text-muted-foreground",
          )}
          style={{ paddingLeft: depth * 12 + 2 }}
          onClick={() => { if (el) onSelectElement(el.id); }}
          title={el ? "点击选中该元素" : "结构节点（元素库未收录）"}
        >
          {hasChildren ? (
            <button type="button" className="grid h-3.5 w-3.5 shrink-0 place-items-center"
              onClick={(e) => { e.stopPropagation(); toggle(node.key); }}>
              <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
            </button>
          ) : <span className="inline-block h-3.5 w-3.5 shrink-0" />}
          <span className={cn("font-mono", el && "font-medium")}>{shortTag(node.tag)}</span>
          {label ? (
            <span className="min-w-0 truncate">
              <span className="text-muted-foreground/70">{label.kind}=</span>
              <span className={cn(el ? "" : "text-muted-foreground")}>&quot;{label.value}&quot;</span>
            </span>
          ) : null}
        </div>
        {open && hasChildren ? node.children.map((c) => renderNode(c, depth + 1)) : null}
      </div>
    );
  };

  if (platform === "web") return null;

  return (
    <div className="flex min-h-0 flex-col rounded-lg border bg-background">
      <div className="flex items-center gap-1.5 border-b px-3 py-2 text-xs font-medium">
        <ListTree className="h-3.5 w-3.5 text-primary" />
        UI 树（应用源）
        <span className="ml-auto font-normal text-muted-foreground">点节点选中元素</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-1.5">
        {snapshotId == null ? (
          <div className="px-2 py-6 text-center text-[11px] text-muted-foreground">选择一个已采集的页面状态查看 UI 树</div>
        ) : docQuery.isLoading ? (
          <div className="grid place-items-center py-8 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /></div>
        ) : docQuery.isError ? (
          <div className="px-2 py-6 text-center text-[11px] text-red-600">该快照未采集到 UI 树</div>
        ) : tree ? (
          <div className="min-w-max">{renderNode(tree, 0)}</div>
        ) : (
          <div className="px-2 py-6 text-center text-[11px] text-muted-foreground">UI 树解析失败</div>
        )}
      </div>
    </div>
  );
}
