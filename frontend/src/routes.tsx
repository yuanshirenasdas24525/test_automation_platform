import { createBrowserRouter } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { AppPackagesPage } from "@/pages/AppPackagesPage";
import { ConfigPage } from "@/pages/ConfigPage";
import { DevicesPage } from "@/pages/DevicesPage";
import { FunctionalCasesPage } from "@/pages/FunctionalCasesPage";
import { HomePage } from "@/pages/HomePage";
import { ProjectDetailPage } from "@/pages/ProjectDetailPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { RequirementsPage } from "@/pages/RequirementsPage";
import { RunsPage } from "@/pages/RunsPage";

/**
 * 路由表。
 * - /projects/:id 是项目详情（模块树 + 自动化用例）
 * - /projects/:id/functional 是功能用例（人工执行）独立编辑器
 * - /runs 执行记录
 * - /devices 设备池（App 自动化）
 * - /config 配置中心
 */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "projects", element: <ProjectsPage /> },
      { path: "projects/:id", element: <ProjectDetailPage /> },
      { path: "projects/:id/functional", element: <FunctionalCasesPage /> },
      { path: "projects/:id/requirements", element: <RequirementsPage /> },
      { path: "runs", element: <RunsPage /> },
      { path: "devices", element: <DevicesPage /> },
      { path: "app-packages", element: <AppPackagesPage /> },
      { path: "config", element: <ConfigPage /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-2xl font-semibold">404</h1>
      <p className="text-sm text-muted-foreground">没找到这个页面。</p>
    </div>
  );
}
