import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FunctionalCasesPage } from "./FunctionalCasesPage";
import { AutomationCasesPage } from "./AutomationCasesPage";
import {
  Apple,
  ArrowLeft,
  Braces,
  ClipboardList,
  FolderKanban,
  Globe,
  Smartphone,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ProjectManagementPage } from "@/pages/ProjectManagementPage";
import { cn } from "@/lib/utils";
import { projectsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import {
  ALL_PROJECT_STACKS,
  type CaseType,
  type ProjectStack,
} from "@/types/domain";

/**
 * 项目详情页：文件管理器风格。
 *
 * 交互：
 *  - 面包屑顶部：项目名 > 模块 A > 模块 B … 点击任意一段返回对应层级
 *  - 主区域：列出当前层级的子模块 + 用例（模块在前），点模块下钻
 *  - 工具栏：新建子模块 / 新建用例 / 导入用例 / 运行整个项目
 *  - 每行尾部三点菜单：编辑、删除、运行、在此之前插入用例
 *
 * 为什么不用递归树？shadcn 里没有现成的 Tree 组件，递归展开得自己写节点 state；
 * 文件管理器风格在后端接口形状（按 parent_id 拉一层）下最贴合，也方便用户理解。
 */

// =============================================================================
// 栈相关工具：v2 起项目可同时启用多个栈，详情页通过顶部 Tab 切栈视图。
// =============================================================================

/** Tab 上的中文展示名。Tab 顺序按 ALL_PROJECT_STACKS（功能 → API → Web → Android → iOS）。 */
const STACK_LABELS: Record<ProjectStack, string> = {
  functional: "功能",
  api: "API",
  web: "Web",
  android: "Android",
  ios: "iOS",
};

/** URL 上的 ?stack=xxx 解析；非法值兜回 "api"。 */
function parseStackParam(raw: unknown): ProjectStack {
  if (!raw) return "api";
  const v = String(Array.isArray(raw) ? raw[0] ?? "" : raw).trim().toLowerCase();
  if ((ALL_PROJECT_STACKS as readonly string[]).includes(v)) {
    return v as ProjectStack;
  }
  return "api";
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();

  // 栈 Tab 状态走 URL（?stack=api/web/app/functional），便于刷新 + 分享 + 浏览器后退。
  // 默认 "api"；下方 effect 会在 stackCounts 拿到后自动校正到"项目实际启用的第一个栈"。
  const [searchParams, setSearchParams] = useSearchParams();
  const activeStack = parseStackParam(searchParams.get("stack"));

  const [caseWorkbenchResetKey, setCaseWorkbenchResetKey] = useState(0);

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });

  /**
   * 项目详情页 Tab 角标用：每种 case_type 的用例数 + 启用栈集合。
   * 拿到后用来：
   *   1) 控制哪些 Tab 显示（只显示 enabled_stacks 内的栈）
   *   2) 校正 URL：如果 URL 带的栈不在 enabled_stacks 内，跳到第一个启用的栈
   *   3) 在每个 Tab 上显示数量 badge
   */
  const stackCountsQuery = useQuery({
    queryKey: queryKeys.projectStackCounts(projectId),
    queryFn: () => projectsApi.stackCounts(projectId),
    enabled: Number.isFinite(projectId),
  });

  // URL 校正：activeStack 不在项目启用栈内时，跳到第一个启用的栈
  // （按 ALL_PROJECT_STACKS 固定顺序：功能 → API → Web → App）
  const enabledStacks = useMemo<ProjectStack[]>(() => {
    const counts = stackCountsQuery.data;
    if (!counts) return [];
    const set = new Set(counts.enabled_stacks);
    return ALL_PROJECT_STACKS.filter((s) => set.has(s));
  }, [stackCountsQuery.data]);

  useEffect(() => {
    if (enabledStacks.length === 0) return;
    if ((activeStack as string) === "management") return; // management is always valid
    if (!enabledStacks.includes(activeStack as ProjectStack)) {
      const next = new URLSearchParams(searchParams);
      next.set("stack", enabledStacks[0]);
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabledStacks, activeStack]);

  /** 切 Tab：写回 URL，并通知内嵌用例工作台回到项目根。 */
  const handleStackChange = (next: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("stack", next);
    setSearchParams(params, { replace: true });
    if (next !== "functional" && next !== "management") {
      setCaseWorkbenchResetKey((key) => key + 1);
    }
  };

  const isFunctionalTab = activeStack === "functional";

  const automationCategory: Exclude<ProjectStack, "functional"> =
    activeStack === "functional" ? "api" : (activeStack as Exclude<ProjectStack, "functional">);

  const isManagementTab = searchParams.get("stack") === "management";

  // ------ 渲染：项目管理 Tab ------
  if (isManagementTab) {
    return (
      <div className="space-y-4 p-6">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Button variant="ghost" size="icon" className="shrink-0" onClick={() => navigate("/projects")}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="font-medium truncate">{projectQuery.data?.name ?? "…"}</span>
          </div>
          <div className="flex shrink-0 items-center gap-2">
          </div>
        </div>
        <StackTabs projectId={projectId} enabledStacks={enabledStacks} counts={stackCountsQuery.data?.counts} active={activeStack} onChange={handleStackChange} loading={stackCountsQuery.isLoading} />
        <ProjectManagementPage />
      </div>
    );
  }

  // ------ 渲染：测试 Tab ------
  if (!Number.isFinite(projectId)) {
    return (
      <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      {/* 顶栏：返回 + 项目名 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => navigate("/projects")}
            title="返回项目列表"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <span className="font-medium truncate">
            {projectQuery.data?.name ?? "…"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2"></div>
      </div>

      {/* Tab 栏 */}
      <StackTabs
        projectId={projectId}
        enabledStacks={enabledStacks}
        counts={stackCountsQuery.data?.counts}
        active={activeStack}
        onChange={handleStackChange}
        loading={stackCountsQuery.isLoading}
      />

      {/* functional Tab：整页内嵌功能用例管理 */}
      {isFunctionalTab ? (
        <FunctionalCasesPage embedded />
      ) : (
        <AutomationCasesPage
          embedded
          caseType={automationCategory}
          resetKey={caseWorkbenchResetKey}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 栈 Tab：v2 起项目可同时启用多个栈，详情页用顶部 Tab 切栈视图。
//
// 角标计数规则（保持和 caseTypesFor() 同步）：
//   - functional Tab：counts.functional
//   - 自动化 Tab（api/web/app）：counts[stack] + counts.mixed
//     mixed 用例会同时出现在它涉及的每个 Tab，所以这里也要把 mixed 计上。
//
// loading 期间渲染 skeleton 占位，不让 Tab 闪现一下又收起。
// 项目只启用 1 个栈时也照样渲染 Tab —— 视觉一致 + 防御后续启用更多栈不需要改 UI。
// ---------------------------------------------------------------------------
/** 每种栈的 lucide 图标 + 主色调（active 态用，主要是边框 / 文字色提亮）。
 * 颜色直接走 Tailwind 调色板而不是 token，因为这里就是要"每种栈一眼能区分"。
 * Android / iOS 当前 ProjectStack 还没拆出来；图标先备好，等 Issue 1 落地直接接。 */
const STACK_VISUAL: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; accent: string }
> = {
  functional: { icon: ClipboardList, accent: "border-amber-500 text-amber-700" },
  api: { icon: Braces, accent: "border-sky-500 text-sky-700" },
  web: { icon: Globe, accent: "border-emerald-500 text-emerald-700" },
  android: { icon: Smartphone, accent: "border-emerald-600 text-emerald-700" },
  ios: { icon: Apple, accent: "border-slate-700 text-slate-800" },
};

function StackTabs({
  projectId: _projectId,
  enabledStacks,
  counts,
  active,
  onChange,
  loading,
}: {
  projectId: number;
  enabledStacks: ProjectStack[];
  counts?: Record<CaseType, number>;
  active: ProjectStack;
  onChange: (next: string) => void;
  loading: boolean;
}) {
  const [searchParams] = useSearchParams();
  const isManagementActive = searchParams.get("stack") === "management";
  if (loading && enabledStacks.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <Skeleton className="h-12 w-32" />
        <Skeleton className="h-12 w-32" />
        <Skeleton className="h-12 w-32" />
      </div>
    );
  }
  if (enabledStacks.length === 0) return null;

  const badgeOf = (stack: ProjectStack): number | null => {
    if (!counts) return null;
    if (stack === "functional") return counts.functional ?? 0;
    return (counts[stack as CaseType] ?? 0) + (counts.mixed ?? 0);
  };

  return (
    <div
      role="tablist"
      className="flex flex-wrap items-stretch gap-1 rounded-lg border bg-muted/40 p-1"
    >
      {/* 项目管理 — 通过 URL ?stack=management 内嵌在 ProjectDetailPage 中 */}
      <button
        type="button"
        role="tab"
        aria-selected={isManagementActive}
        onClick={() => onChange("management")}
        className={cn(
          "group relative flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-all",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          isManagementActive
            ? "border-t-[3px] bg-background font-semibold shadow-sm border-red-500 text-red-800"
            : "border-t-[3px] border-transparent text-muted-foreground hover:bg-background/60 hover:text-foreground",
        )}
      >
        <FolderKanban className="h-4 w-4" />
        <span>项目管理</span>
      </button>
      {enabledStacks.map((s) => {
        const isActive = !isManagementActive && active === s;
        const badge = badgeOf(s);
        const visual = STACK_VISUAL[s] ?? STACK_VISUAL.api;
        const Icon = visual.icon;
        return (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(s)}
            className={cn(
              "group relative flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-all",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              isActive
                ? cn(
                    "border-t-[3px] bg-background font-semibold shadow-sm",
                    visual.accent,
                  )
                : "border-t-[3px] border-transparent text-muted-foreground hover:bg-background/60 hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" />
            <span>{STACK_LABELS[s]}</span>
            {badge !== null ? (
              <span
                className={cn(
                  "ml-1 rounded-full px-1.5 py-0.5 text-xs font-normal tabular-nums",
                  isActive
                    ? "bg-muted text-foreground"
                    : "bg-background text-muted-foreground",
                )}
              >
                {badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
