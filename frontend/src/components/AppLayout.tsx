import { useEffect } from "react";
import { Navigate, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Code2,
  FolderKanban,
  LayoutDashboard,
  Package,
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
  const backendOrigin = import.meta.env.DEV
    ? "http://127.0.0.1:54351"
    : window.location.origin;
  const { user, setUser } = useCurrentUser();

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
      <div className="flex h-screen bg-muted/30">
        <aside className="flex w-56 flex-col border-r bg-background">
          <div className="flex h-14 items-center gap-2 border-b px-4">
            <img
              src="/brand-mark.svg"
              alt=""
              className="h-9 w-9 shrink-0 rounded-md"
            />
            <div className="text-sm font-semibold leading-tight">
              自动化测试平台
              <div className="text-xs font-normal text-muted-foreground">v2 · React</div>
            </div>
          </div>
          <nav className="flex-1 space-y-1 p-3 text-sm">
            {NAV.map((item) => {
              const Icon = item.icon;
              // 同一 to 命中 pathname 即激活；带 query/hash 的访问也算同一节点。
              // 子路由也算激活：/workspace/dev、/projects/12 都让对应顶级 NAV 高亮。
              const active =
                current === item.to ||
                pathname === item.to ||
                (item.to !== "/" && pathname.startsWith(item.to + "/"));
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
            后端：<code className="break-all font-mono">{backendOrigin}</code>
          </div>
        </aside>
        <main className="flex flex-1 flex-col overflow-hidden">
          <header
            data-app-header
            className="flex h-12 shrink-0 items-center justify-end gap-2 border-b bg-background px-4"
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
