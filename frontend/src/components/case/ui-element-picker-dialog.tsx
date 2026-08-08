import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Layers3, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { uiRecordingsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UiElement, UiPlatform } from "@/types/domain";

const ROUTE_LABELS: Record<string, string> = {
  "/login": "登录页",
  "/workspace/admin": "管理员工作台",
  "/projects": "项目管理",
  "/runs": "执行记录",
  "/devices": "设备池",
  "/app-packages": "App 包管理",
  "/scripts": "脚本库",
};

function pickerPageName(pageKey: string, capturedName: string): string {
  const slash = pageKey.indexOf("/");
  const route = slash >= 0 ? pageKey.slice(slash) : pageKey;
  const [pathname, search = ""] = route.split("?", 2);
  const identity = [...new URLSearchParams(search).entries()]
    .slice(0, 3)
    .map(([key, value]) => `${key}=${value}`)
    .join(" · ");
  if (capturedName && capturedName !== "自动化测试平台") {
    return identity ? `${capturedName} · ${identity}` : capturedName;
  }
  const projectMatch = /^\/projects\/(\d+)$/.exec(pathname);
  const versionMatch = /^\/projects\/(\d+)\/versions\/(\d+)$/.exec(pathname);
  const baseName = ROUTE_LABELS[pathname]
    ?? (projectMatch ? `项目 #${projectMatch[1]}` : null)
    ?? (versionMatch ? `项目 #${versionMatch[1]} · 版本 #${versionMatch[2]}` : null)
    ?? pathname;
  return identity ? `${baseName} · ${identity}` : baseName;
}

export function UiElementPickerDialog({
  open,
  projectId,
  platform,
  onOpenChange,
  onSelect,
}: {
  open: boolean;
  projectId: number;
  platform: UiPlatform;
  onOpenChange: (open: boolean) => void;
  onSelect: (element: UiElement, by: string, locator: string) => void;
}) {
  const [keyword, setKeyword] = useState("");
  const [pageKey, setPageKey] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["ui-element-picker", projectId, platform],
    queryFn: () => uiRecordingsApi.listElements({ projectId, platform }),
    enabled: open,
  });
  const pages = useMemo(() => {
    const groups = new Map<string, { name: string; items: UiElement[] }>();
    for (const element of query.data ?? []) {
      const group = groups.get(element.page_key) ?? {
        name: pickerPageName(element.page_key, element.page_name),
        items: [],
      };
      group.items.push(element);
      groups.set(element.page_key, group);
    }
    return [...groups.entries()].map(([key, value]) => ({ key, ...value }));
  }, [query.data]);
  const activePageKey = pageKey ?? pages[0]?.key ?? null;
  const elements = (pages.find((item) => item.key === activePageKey)?.items ?? []).filter((element) => {
    const token = keyword.trim().toLocaleLowerCase();
    return !token || `${element.semantic_name} ${element.element_type} ${element.locators.map((item) => item.locator).join(" ")}`.toLocaleLowerCase().includes(token);
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[72vh] max-w-4xl flex-col overflow-hidden p-0">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle>从项目元素库选择</DialogTitle>
          <DialogDescription>选择页面和元素后，自动填入已验证的主定位器</DialogDescription>
        </DialogHeader>
        <div className="grid min-h-0 flex-1 grid-cols-[240px_1fr]">
          <aside className="overflow-y-auto border-r bg-muted/15 p-3">
            {pages.map((page) => (
              <button
                type="button"
                key={page.key}
                onClick={() => setPageKey(page.key)}
                className={cn("mb-1 w-full rounded-lg px-3 py-2 text-left text-xs", activePageKey === page.key ? "bg-primary/10 text-primary" : "hover:bg-muted")}
              >
                <div className="truncate font-medium">{page.name}</div>
                <div className="mt-1 truncate text-[10px] text-muted-foreground">{page.key} · {page.items.length} 元素</div>
              </button>
            ))}
          </aside>
          <main className="flex min-h-0 flex-col p-4">
            <div className="relative mb-3"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" /><Input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索元素或定位器" className="pl-9" /></div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {elements.map((element) => {
                const preferred = [...element.locators].sort((left, right) => Number(right.is_primary) - Number(left.is_primary) || Number(right.is_unique) - Number(left.is_unique) || right.score - left.score)[0];
                return (
                  <div key={element.id} className="mb-2 flex items-center gap-3 rounded-lg border p-3">
                    <div className="grid h-9 w-9 place-items-center rounded-lg bg-muted"><Layers3 className="h-4 w-4" /></div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-sm font-medium">{element.semantic_name}{element.status === "verified" ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : null}</div>
                      <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{preferred ? `${preferred.strategy}: ${preferred.locator}` : "无可用定位器"}</div>
                    </div>
                    <Button size="sm" disabled={!preferred} onClick={() => {
                      if (!preferred) return;
                      onSelect(element, preferred.strategy, preferred.locator);
                      onOpenChange(false);
                    }}>选择</Button>
                  </div>
                );
              })}
              {!elements.length ? <div className="py-16 text-center text-xs text-muted-foreground">当前页面没有匹配元素</div> : null}
            </div>
          </main>
        </div>
      </DialogContent>
    </Dialog>
  );
}
