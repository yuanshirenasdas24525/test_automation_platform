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
  projects: (type?: string) =>
    type ? (["projects", { type }] as const) : (["projects"] as const),
  project: (id: number) => ["project", id] as const,
  content: (projectId: number, parentId?: number | null) =>
    ["content", projectId, parentId ?? null] as const,
  module: (moduleId: number) => ["module", moduleId] as const,
  case: (caseId: number) => ["case", caseId] as const,
  config: (category?: string) => ["config", category ?? "all"] as const,
  configSchema: (category: string) => ["config", "schema", category] as const,
  reports: (params: Record<string, unknown>) => ["reports", params] as const,
  report: (id: number) => ["report", id] as const,
  systemServices: () => ["system", "services"] as const,
  devices: (filters?: Record<string, string | undefined>) =>
    filters && Object.keys(filters).length > 0
      ? (["devices", filters] as const)
      : (["devices"] as const),
  device: (id: number) => ["device", id] as const,
  devicePools: () => ["devices", "pools"] as const,
};
