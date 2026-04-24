import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  FolderKanban,
  LayoutDashboard,
  PlayCircle,
  Settings,
  Smartphone,
} from "lucide-react";

import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
};

const NAV: NavItem[] = [
  { to: "/", label: "工作台", icon: LayoutDashboard },
  { to: "/projects?type=api", label: "项目", icon: FolderKanban },
  { to: "/runs", label: "执行记录", icon: PlayCircle },
  { to: "/devices", label: "设备池", icon: Smartphone },
  { to: "/config", label: "配置中心", icon: Settings },
];

export function AppLayout() {
  const { pathname, search } = useLocation();
  const current = pathname + search;

  return (
    <div className="flex h-screen bg-muted/30">
      <aside className="flex w-56 flex-col border-r bg-background">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <PlayCircle className="h-5 w-5" />
          </div>
          <div className="text-sm font-semibold leading-tight">
            自动化测试平台
            <div className="text-xs font-normal text-muted-foreground">v2 · React</div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 p-3 text-sm">
          {NAV.map((item) => {
            const Icon = item.icon;
            // 精确匹配 pathname + search，避免 "/projects?type=api" 和 "/projects?type=web" 互相高亮
            const active = current === item.to || pathname === item.to;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 transition-colors",
                  active
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="flex-1">{item.label}</span>
                {item.badge ? (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {item.badge}
                  </span>
                ) : null}
              </NavLink>
            );
          })}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">
          后端：<code className="font-mono">127.0.0.1:54351</code>
        </div>
      </aside>
      <main className="flex flex-1 flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
