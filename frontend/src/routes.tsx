import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { AppPackagesPage } from "@/pages/AppPackagesPage";
import { ConfigPage } from "@/pages/ConfigPage";
import { DevicesPage } from "@/pages/DevicesPage";
import { FunctionalCasesPage } from "@/pages/FunctionalCasesPage";
import { HomePage } from "@/pages/HomePage";
import { ProjectDetailPage } from "@/pages/ProjectDetailPage";
import { ProjectManagementPage } from "@/pages/ProjectManagementPage";
import { ProjectVersionDetailPage } from "@/pages/ProjectVersionDetailPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { RequirementsPage } from "@/pages/RequirementsPage";
import { RunsPage } from "@/pages/RunsPage";
import { TaskDetailPage } from "@/pages/tasks/TaskDetailPage";
import { TaskListPage } from "@/pages/tasks/TaskListPage";
import { VersionBoardPage } from "@/pages/versions/VersionBoardPage";
import {
  WorkspaceRedirect,
  WorkspaceRoute,
} from "@/pages/workspace/WorkspaceRoute";

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
      { index: true, element: <Navigate to="/workspace" replace /> },
      { path: "dashboard", element: <HomePage /> },
      { path: "projects", element: <ProjectsPage /> },
      { path: "projects/:id", element: <ProjectDetailPage /> },
      { path: "projects/:id/functional", element: <FunctionalCasesPage /> },
      { path: "projects/:id/management", element: <ProjectManagementPage /> },
      { path: "projects/:id/versions/:vid", element: <ProjectVersionDetailPage /> },
      { path: "projects/:id/versions/:vid/board", element: <VersionBoardPage /> },
      { path: "projects/:id/requirements", element: <RequirementsPage /> },
      { path: "workspace", element: <WorkspaceRedirect /> },
      { path: "workspace/:role", element: <WorkspaceRoute /> },
      { path: "tasks", element: <TaskListPage /> },
      { path: "tasks/:id", element: <TaskDetailPage /> },
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
