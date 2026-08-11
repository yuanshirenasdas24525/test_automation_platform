import { Suspense, lazy, type ReactNode } from "react";
import {
  Navigate,
  createBrowserRouter,
  isRouteErrorResponse,
  useRouteError,
} from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { isChunkLoadError } from "@/lib/chunk-recovery";

const AppPackagesPage = lazy(() => import("@/pages/AppPackagesPage").then((m) => ({ default: m.AppPackagesPage })));
const ChangePasswordPage = lazy(() => import("@/pages/ChangePasswordPage").then((m) => ({ default: m.ChangePasswordPage })));
const DevicesPage = lazy(() => import("@/pages/DevicesPage").then((m) => ({ default: m.DevicesPage })));
const FunctionalCasesPage = lazy(() => import("@/pages/FunctionalCasesPage").then((m) => ({ default: m.FunctionalCasesPage })));
const HomePage = lazy(() => import("@/pages/HomePage").then((m) => ({ default: m.HomePage })));
const LoginPage = lazy(() => import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const PerformanceConcurrencyPage = lazy(() => import("@/pages/PerformanceConcurrencyPage").then((m) => ({ default: m.PerformanceConcurrencyPage })));
const PerformanceRequirementPage = lazy(() => import("@/pages/PerformanceRequirementPage").then((m) => ({ default: m.PerformanceRequirementPage })));
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
 * - /scripts 全局脚本库
 */
export const router = createBrowserRouter([
  // /login 不挂在 AppLayout 下：登录页是无侧栏 / 无 header 的全屏页。
  // AppLayout 内部会做"未登录 → /login"重定向，这里就不需要再包守卫。
  {
    path: "/login",
    element: lazyPage(<LoginPage />),
    errorElement: <RouteErrorPage />,
  },
  {
    path: "/",
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Navigate to="/workspace" replace /> },
      { path: "dashboard", element: lazyPage(<HomePage />) },
      { path: "projects", element: lazyPage(<ProjectsPage />) },
      { path: "projects/:id", element: lazyPage(<ProjectDetailPage />) },
      { path: "projects/:id/performance", element: lazyPage(<PerformanceRequirementPage />) },
      { path: "projects/:id/performance/concurrency", element: lazyPage(<PerformanceConcurrencyPage />) },
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
      { path: "config", element: <Navigate to="/projects" replace /> },
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

function RouteErrorPage() {
  const error = useRouteError();
  const chunkError = isChunkLoadError(error);
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText || error.data || "路由加载失败"}`
    : error instanceof Error
      ? error.message
      : String(error || "未知错误");

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/20 p-6">
      <div className="w-full max-w-lg rounded-2xl border bg-background p-8 text-center shadow-sm">
        <h1 className="text-xl font-semibold">
          {chunkError ? "页面版本已经更新" : "页面暂时无法加载"}
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          {chunkError
            ? "当前页面仍在使用旧版本资源。重新加载后会自动切换到最新版本，已填写的数据可能需要重新确认。"
            : "页面加载时遇到了异常，你可以重新加载或返回上一页。"}
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            重新加载
          </button>
          <button
            type="button"
            onClick={() => window.history.back()}
            className="rounded-md border px-4 py-2 text-sm font-medium"
          >
            返回上一页
          </button>
        </div>
        <details className="mt-6 text-left text-xs text-muted-foreground">
          <summary className="cursor-pointer">查看错误详情</summary>
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3">{detail}</pre>
        </details>
      </div>
    </div>
  );
}
