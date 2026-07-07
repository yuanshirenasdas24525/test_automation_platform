import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";

const AppPackagesPage = lazy(() => import("@/pages/AppPackagesPage").then((m) => ({ default: m.AppPackagesPage })));
const ChangePasswordPage = lazy(() => import("@/pages/ChangePasswordPage").then((m) => ({ default: m.ChangePasswordPage })));
const ConfigPage = lazy(() => import("@/pages/ConfigPage").then((m) => ({ default: m.ConfigPage })));
const DevicesPage = lazy(() => import("@/pages/DevicesPage").then((m) => ({ default: m.DevicesPage })));
const FunctionalCasesPage = lazy(() => import("@/pages/FunctionalCasesPage").then((m) => ({ default: m.FunctionalCasesPage })));
const HomePage = lazy(() => import("@/pages/HomePage").then((m) => ({ default: m.HomePage })));
const LoginPage = lazy(() => import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const ProjectDetailPage = lazy(() => import("@/pages/ProjectDetailPage").then((m) => ({ default: m.ProjectDetailPage })));
const ProjectManagementPage = lazy(() => import("@/pages/ProjectManagementPage").then((m) => ({ default: m.ProjectManagementPage })));
const ProjectVersionDetailPage = lazy(() => import("@/pages/ProjectVersionDetailPage").then((m) => ({ default: m.ProjectVersionDetailPage })));
const ProjectsPage = lazy(() => import("@/pages/ProjectsPage").then((m) => ({ default: m.ProjectsPage })));
const RequirementsPage = lazy(() => import("@/pages/RequirementsPage").then((m) => ({ default: m.RequirementsPage })));
const RunsPage = lazy(() => import("@/pages/RunsPage").then((m) => ({ default: m.RunsPage })));
const ScriptLibraryPage = lazy(() => import("@/pages/ScriptLibraryPage").then((m) => ({ default: m.ScriptLibraryPage })));
const TaskDetailPage = lazy(() => import("@/pages/tasks/TaskDetailPage").then((m) => ({ default: m.TaskDetailPage })));
const TaskListPage = lazy(() => import("@/pages/tasks/TaskListPage").then((m) => ({ default: m.TaskListPage })));
const VersionBoardPage = lazy(() => import("@/pages/versions/VersionBoardPage").then((m) => ({ default: m.VersionBoardPage })));
const WorkspaceRedirect = lazy(() => import("@/pages/workspace/WorkspaceRoute").then((m) => ({ default: m.WorkspaceRedirect })));
const WorkspaceRoute = lazy(() => import("@/pages/workspace/WorkspaceRoute").then((m) => ({ default: m.WorkspaceRoute })));

function PageLoader() {
  return (
    <div className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
      加载中...
    </div>
  );
}

function lazyPage(element: ReactNode) {
  return <Suspense fallback={<PageLoader />}>{element}</Suspense>;
}

/**
 * 路由表。
 * - /projects/:id 是项目详情（模块树 + 自动化用例）
 * - /projects/:id/functional 是功能用例（人工执行）独立编辑器
 * - /runs 执行记录
 * - /devices 设备池（App 自动化）
 * - /config 配置中心
 * - /scripts 全局脚本库
 */
export const router = createBrowserRouter([
  // /login 不挂在 AppLayout 下：登录页是无侧栏 / 无 header 的全屏页。
  // AppLayout 内部会做"未登录 → /login"重定向，这里就不需要再包守卫。
  { path: "/login", element: lazyPage(<LoginPage />) },
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/workspace" replace /> },
      { path: "dashboard", element: lazyPage(<HomePage />) },
      { path: "projects", element: lazyPage(<ProjectsPage />) },
      { path: "projects/:id", element: lazyPage(<ProjectDetailPage />) },
      { path: "projects/:id/functional", element: lazyPage(<FunctionalCasesPage />) },
      { path: "projects/:id/management", element: lazyPage(<ProjectManagementPage />) },
      { path: "projects/:id/versions/:vid", element: lazyPage(<ProjectVersionDetailPage />) },
      { path: "projects/:id/versions/:vid/board", element: lazyPage(<VersionBoardPage />) },
      { path: "projects/:id/requirements", element: lazyPage(<RequirementsPage />) },
      { path: "workspace", element: lazyPage(<WorkspaceRedirect />) },
      { path: "workspace/:role", element: lazyPage(<WorkspaceRoute />) },
      { path: "tasks", element: lazyPage(<TaskListPage />) },
      { path: "tasks/:id", element: lazyPage(<TaskDetailPage />) },
      { path: "runs", element: lazyPage(<RunsPage />) },
      { path: "devices", element: lazyPage(<DevicesPage />) },
      { path: "app-packages", element: lazyPage(<AppPackagesPage />) },
      { path: "config", element: lazyPage(<ConfigPage />) },
      { path: "scripts", element: lazyPage(<ScriptLibraryPage />) },
      { path: "projects/:id/scripts", element: lazyPage(<ScriptLibraryPage />) },
      { path: "change-password", element: lazyPage(<ChangePasswordPage />) },
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
