import { QueryClient } from "@tanstack/react-query";

/** 全局 QueryClient：合理的默认 staleTime 减少无谓 refetch；错误自己用 ApiError 包过了。 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10 * 1000,
      gcTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
    mutations: {
      retry: 0,
    },
  },
});

/** 查询键工厂 —— 集中管理，防止字符串拼写错。 */
export const queryKeys = {
  /**
   * 项目列表。`stack` 参数（可选）按后端 `?stack=` 过滤。
   * v1 时这个参数叫 `type`（项目=单一栈），v2 起改为 `stack`，键名也跟着变。
   */
  projects: (stack?: string) =>
    stack ? (["projects", { stack }] as const) : (["projects"] as const),
  project: (id: number) => ["project", id] as const,
  /** 项目详情页 Tab 角标用：每种 case_type 的用例数量。 */
  projectStackCounts: (id: number) => ["projects", id, "stack_counts"] as const,
  /**
   * 内容树（模块 + 用例）。caseType 现在是查询键的一部分 —— 项目详情页
   * 在不同栈 Tab 下显示不同用例集合，但共用同一棵模块树。
   */
  content: (
    projectId: number,
    parentId?: number | null,
    caseType?: string | string[] | null,
  ) => {
    const ct = Array.isArray(caseType)
      ? caseType.filter(Boolean).sort().join(",")
      : caseType || "";
    return ["content", projectId, parentId ?? null, ct] as const;
  },
  module: (moduleId: number) => ["module", moduleId] as const,
  case: (caseId: number) => ["case", caseId] as const,
  /** 功能用例：列表（按 module 或 project，可带状态过滤 + 分页）。 */
  functionalCases: (filters: Record<string, unknown>) =>
    ["functional_cases", filters] as const,
  functionalCase: (id: number) => ["functional_cases", id] as const,
  /** 某条功能用例的执行历史。 */
  functionalRuns: (caseId: number) =>
    ["functional_cases", caseId, "runs"] as const,
  /** 一个项目下的"批次概览"（回归测试看板用）。 */
  functionalBatches: (projectId: number) =>
    ["functional_cases", "batches", projectId] as const,
  config: (category?: string) => ["config", category ?? "all"] as const,
  configSchema: (category: string) => ["config", "schema", category] as const,
  scripts: (filters?: Record<string, unknown>) =>
    filters && Object.keys(filters).length > 0
      ? (["scripts", filters] as const)
      : (["scripts"] as const),
  reports: (params: Record<string, unknown>) => ["reports", params] as const,
  report: (id: number) => ["report", id] as const,
  systemServices: () => ["system", "services"] as const,
  devices: (filters?: Record<string, string | undefined>) =>
    filters && Object.keys(filters).length > 0
      ? (["devices", filters] as const)
      : (["devices"] as const),
  device: (id: number) => ["device", id] as const,
  devicePools: () => ["devices", "pools"] as const,
  appPackages: (filters?: Record<string, string | undefined>) =>
    filters && Object.keys(filters).length > 0
      ? (["app_packages", filters] as const)
      : (["app_packages"] as const),
  /** AI 任务历史 / 单条 / 项目下的需求列表。 */
  aiRuns: (filters?: Record<string, unknown>) =>
    filters && Object.keys(filters).length > 0
      ? (["ai_runs", filters] as const)
      : (["ai_runs"] as const),
  aiRun: (id: number) => ["ai_runs", id] as const,
  requirements: (
    projectId: number,
    filters?: Record<string, string | undefined>,
  ) =>
    filters && Object.keys(filters).length > 0
      ? (["requirements", projectId, filters] as const)
      : (["requirements", projectId] as const),
  requirement: (id: number) => ["requirement", id] as const,
  /** M6：AI 模型连接（配置中心）+ 需求分析文档 + 版本。 */
  aiModels: () => ["ai_models"] as const,
  analysisDocs: (requirementId: number) =>
    ["analysis_docs", requirementId] as const,
  analysisDoc: (docId: number) => ["analysis_doc", docId] as const,
  analysisVersions: (docId: number) =>
    ["analysis_doc", docId, "versions"] as const,
  /** M7：AI 用例草稿 + 一键生成任务进度。 */
  aiCaseDrafts: (filters?: Record<string, unknown>) =>
    filters && Object.keys(filters).length > 0
      ? (["ai_case_drafts", filters] as const)
      : (["ai_case_drafts"] as const),
  aiCaseGenerationRun: (runId: number) =>
    ["ai_case_generation_run", runId] as const,
  /** 全局任务看板：进行中的异步任务（GET /api/tasks/in-progress）。 */
  tasksInProgress: (projectId?: number | null) =>
    projectId != null
      ? (["tasks_in_progress", { project_id: projectId }] as const)
      : (["tasks_in_progress"] as const),
};
