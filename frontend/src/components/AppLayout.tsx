import { useEffect, useState } from "react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Code2,
  FolderKanban,
  LayoutDashboard,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  PlayCircle,
  Smartphone,
} from "lucide-react";

import { CurrentUserSwitcher } from "@/components/CurrentUserSwitcher";
import { FloatingTaskWidget } from "@/components/FloatingTaskWidget";
import { useCurrentUser } from "@/lib/current-user";
import { AUTH_EXPIRED_EVENT } from "@/lib/api";
import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
};

// v2 起项目卡片不再按栈分桶，去掉 ?type=api 旧 query；新版项目页本身就展示所有项目。
// PM M3：根路径重定向到 /workspace；旧 HomePage 仍可通过 /dashboard 访问，但 NAV 不展示。
const NAV: NavItem[] = [
  { to: "/workspace", label: "工作台", icon: LayoutDashboard },
  { to: "/projects", label: "项目", icon: FolderKanban },
  { to: "/runs", label: "执行记录", icon: PlayCircle },
  { to: "/devices", label: "设备池", icon: Smartphone },
  { to: "/app-packages", label: "App 包管理", icon: Package },
  { to: "/scripts", label: "脚本库", icon: Code2 },
];

export function AppLayout() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const current = pathname + search;
  const { user, setUser } = useCurrentUser();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("app-sidebar-collapsed") === "1");
  useEffect(() => {
    localStorage.setItem("app-sidebar-collapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  useEffect(() => {
    const handleAuthExpired = () => {
      setUser(null);
      navigate("/login", { replace: true });
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired);
  }, [navigate, setUser]);

  // 没登录用户 → 跳到 /login。CurrentUserProvider 已经提到了 main.tsx，
  // /login 页面也能拿到同一份 Context 来 setUser。
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <>
      <div
        className="flex h-screen bg-muted/30"
        style={{ ["--app-sidebar-w" as string]: collapsed ? "3.5rem" : "14rem" }}
      >
        <aside className={cn("flex shrink-0 flex-col border-r bg-background transition-[width] duration-200", collapsed ? "w-14" : "w-56")}>
          <div className={cn("flex h-14 items-center border-b", collapsed ? "justify-center px-0" : "gap-2 px-4")}>
            <img src="/brand-mark.svg" alt="" className="h-9 w-9 shrink-0 rounded-md" />
            {!collapsed ? (
              <div className="text-sm font-semibold leading-tight">
                自动化测试平台
                <div className="text-xs font-normal text-muted-foreground">v2 · React</div>
              </div>
            ) : null}
          </div>
          <nav className="flex-1 space-y-1 p-3 text-sm">
            {NAV.map((item) => {
              const Icon = item.icon;
              const active =
                current === item.to ||
                pathname === item.to ||
                (item.to !== "/" && pathname.startsWith(item.to + "/"));
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "flex items-center rounded-md py-2 transition-colors",
                    collapsed ? "justify-center px-0" : "gap-3 px-3",
                    active
                      ? "bg-accent font-medium text-accent-foreground"
                      : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed ? <span className="flex-1">{item.label}</span> : null}
                  {!collapsed && item.badge ? (
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                      {item.badge}
                    </span>
                  ) : null}
                </NavLink>
              );
            })}
          </nav>
          {/* 收起 / 展开 */}
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
            className={cn(
              "flex items-center py-2 text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground",
              collapsed ? "justify-center" : "gap-3 px-3",
            )}
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <><PanelLeftClose className="h-4 w-4" /><span className="text-sm">收起</span></>}
          </button>
        </aside>
        <main className="flex flex-1 flex-col overflow-hidden">
          <header
            data-app-header
            className="flex h-14 shrink-0 items-center justify-end gap-2 border-b bg-background px-4"
          >
            <CurrentUserSwitcher />
          </header>
          <div className="flex-1 overflow-y-auto">
            <Outlet />
          </div>
          <FloatingTaskWidget />
        </main>
      </div>
    </>
  );
}
