/**
 * 薄薄一层 fetch 封装。所有后端接口都返回 `{status, data?, message?}` 信封，
 * 这里统一帮你剥壳：业务失败（status==="error"）和网络错误都抛成 `ApiError`，
 * 这样上游用 try/catch 或 TanStack Query 都省事。
 */
import type {
  AiBugFixResponse,
  AiCaseDraft,
  AiCaseDraftStatus,
  AiCaseDraftUpdatePayload,
  AiFeature,
  AiGenerationHistoryDetail,
  AiGenerationHistoryItem,
  AiGeneratedCase,
  AiOutlinePoint,
  AiModelConfig,
  AiModelConfigUpsert,
  AiModelTestResult,
  AiRun,
  AiRunStatus,
  AiCaseFlag,
  AiFlagClearReason,
  AiFlagCounts,
  AiFlagType,
  ApiCaseEditRecord,
  ApiCaseLatestRunDetail,
  ApiCaseListResponse,
  ApiRunStatus,
  ApiTestHistoryReport,
  AnalysisDiffResponse,
  AnalysisDocument,
  AnalysisTriggerResponse,
  AnalysisVersion,
  BugFixAgent,
  CaseGenerationRun,
  CaseGenerationTriggerPayload,
  CaseGenerationTriggerResponse,
  CommitDraftsPayload,
  CommitDraftsResult,
  ApiEnvelope,
  ApplySummary,
  CaseType,
  ChangePasswordRequest,
  ChangePlan,
  ContentNode,
  FunctionalBatchMarkPayload,
  FunctionalBatchSummary,
  FunctionalCase,
  FunctionalCaseCreate,
  FunctionalCaseEditRecord,
  FunctionalCaseRun,
  FunctionalCaseUpdate,
  FunctionalMarkPayload,
  FunctionalRunStatus,
  FunctionalTestHistoryRun,
  LoginRequest,
  LoginResponse,
  Module,
  ModuleCreate,
  Project,
  ProjectCreate,
  ProjectAiOverviewResp,
  ProjectStack,
  ProjectStackCounts,
  ProjectVersion,
  ReorderItem,
  Attachment,
  Requirement,
  RequirementAcceptPayload,
  RequirementCreate,
  RequirementEditHistory,
  RequirementListFilters,
  CoverageResult,
  RequirementRollbackPayload,
  RequirementRollbackResult,
  RequirementSplitItem,
  RequirementUpdate,
  Role,
  RunTestRequest,
  RunTestResult,
  ScriptItem,
  ScriptKind,
  ScriptPayload,
  ScriptScope,
  ScriptTestPayload,
  ScriptTestResult,
  Task,
  TaskCreate,
  TaskFromTestFailurePayload,
  TaskListFilters,
  TaskUpdate,
  TestCaseCreate,
  TestCaseDetail,
  User,
  UserCreate,
  UserSession,
  UserUpdate,
  VersionBoard,
  VersionCase,
  VersionCreate,
  VersionPickerItem,
  VersionTestSummary,
  VersionUpdate,
  UiElement,
  UiAiExplorationStatus,
  UiOfflineReplay,
  UiPageSnapshot,
  UiPlatform,
  UiRecordingEvent,
  UiRecordedAction,
  UiRecordingContextBundle,
  UiExecutionContextBundle,
  UiRecordingSession,
  UiRecordingStepDraft,
  KnowledgeDoc,
  KnowledgeDocCreate,
  KnowledgeDocUpdate,
} from "@/types/domain";

export class ApiError extends Error {
  constructor(
    public readonly message: string,
    public readonly status?: number,
    public readonly payload?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function formatApiErrorDetail(value: unknown): string | null {
  if (typeof value === "string") {
    return value.trim() || null;
  }
  if (Array.isArray(value)) {
    const messages = value
      .map((item) => formatApiErrorDetail(item))
      .filter((item): item is string => Boolean(item));
    return messages.length > 0 ? messages.join("；") : null;
  }
  if (!value || typeof value !== "object") {
    return null;
  }

  const issue = value as Record<string, unknown>;
  const message = formatApiErrorDetail(issue.msg ?? issue.message);
  if (!message) {
    return null;
  }

  const location = Array.isArray(issue.loc)
    ? issue.loc
        .map((part) => String(part))
        .filter((part) => part !== "body")
        .join(".")
    : "";
  return location ? `${location}：${message}` : message;
}

// ---------------------------------------------------------------------------
// Token 管理
// ---------------------------------------------------------------------------
const TOKEN_KEY = "pm.accessToken";
const LEGACY_TOKEN_KEY = "pm.authToken";
const REFRESH_TOKEN_KEY = "pm.refreshToken";
const DEVICE_ID_KEY = "pm.deviceId";
export const AUTH_EXPIRED_EVENT = "pm:auth-expired";

let refreshPromise: Promise<string | null> | null = null;

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return (
    window.localStorage.getItem(TOKEN_KEY) ||
    window.localStorage.getItem(LEGACY_TOKEN_KEY)
  );
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token);
    window.localStorage.removeItem(LEGACY_TOKEN_KEY);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(LEGACY_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setRefreshToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, token);
  } else {
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

function notifyAuthExpired() {
  setToken(null);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

function getDeviceId(): string | null {
  if (typeof window === "undefined") return null;
  const existing = window.localStorage.getItem(DEVICE_ID_KEY);
  if (existing) return existing;
  const next =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  window.localStorage.setItem(DEVICE_ID_KEY, next);
  return next;
}

function detectBrowser(userAgent: string) {
  if (/Edg\//.test(userAgent)) return "Edge";
  if (/Chrome\//.test(userAgent) && !/Chromium\//.test(userAgent)) return "Chrome";
  if (/Safari\//.test(userAgent) && !/Chrome\//.test(userAgent)) return "Safari";
  if (/Firefox\//.test(userAgent)) return "Firefox";
  return "Browser";
}

function detectOs(userAgent: string, platform: string) {
  if (/iPhone|iPad|iPod/.test(userAgent)) return "iOS";
  if (/Android/.test(userAgent)) return "Android";
  if (/Mac/.test(platform)) return "macOS";
  if (/Win/.test(platform)) return "Windows";
  if (/Linux/.test(platform)) return "Linux";
  return platform || "Unknown";
}

function buildClientInfo(): NonNullable<LoginRequest["client"]> {
  if (typeof window === "undefined") {
    return { client_type: "web", session_kind: "password_login" };
  }
  const userAgent = window.navigator.userAgent;
  const platform = window.navigator.platform;
  const osName = detectOs(userAgent, platform);
  const browserName = detectBrowser(userAgent);
  return {
    session_kind: "password_login",
    client_type: "web",
    client_name: browserName,
    app_version: "web",
    platform: osName.toLowerCase(),
    device_id: getDeviceId(),
    device_name: platform || osName,
    os_name: osName,
    browser_name: browserName,
  };
}

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    notifyAuthExpired();
    return null;
  }
  if (!refreshPromise) {
    refreshPromise = (async () => {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      const payload = (await res.json().catch(() => null)) as
        | ApiEnvelope<{ access_token?: string }>
        | null;
      if (!res.ok || !payload || payload.status === "error") {
        notifyAuthExpired();
        return null;
      }
      const nextToken = payload.data?.access_token || null;
      if (!nextToken) {
        notifyAuthExpired();
        return null;
      }
      setToken(nextToken);
      return nextToken;
    })().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

type RequestInitJSON = Omit<RequestInit, "body"> & {
  body?: unknown;
};

async function fetchWithAuth(
  path: string,
  init: RequestInit = {},
  retryOnAuth = true,
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(path, { ...init, headers });
  if (res.status === 401 && retryOnAuth) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return fetchWithAuth(path, init, false);
    }
  }
  return res;
}

async function request<T>(
  path: string,
  init: RequestInitJSON = {},
  retryOnAuth = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (!isFormData && init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(path, {
    ...init,
    headers,
    body: isFormData
      ? (init.body as FormData)
      : init.body !== undefined
        ? JSON.stringify(init.body)
        : undefined,
  });

  if (
    res.status === 401 &&
    retryOnAuth &&
    path !== "/api/auth/login" &&
    path !== "/api/auth/refresh"
  ) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, init, false);
    }
  }

  const text = await res.text();
  let payload: ApiEnvelope<T> | T | null = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      // 非 JSON 响应：报告 HTML 等场景
    }
  }

  if (!res.ok) {
    const errorPayload =
      payload && typeof payload === "object"
        ? (payload as Record<string, unknown>)
        : null;
    const msg =
      formatApiErrorDetail(errorPayload?.detail) ||
      formatApiErrorDetail(errorPayload?.message) ||
      `HTTP ${res.status} ${res.statusText}`;
    throw new ApiError(msg, res.status, payload);
  }

  // 后端大多数接口都裹 {status, data, message}，这里剥壳
  if (payload && typeof payload === "object" && "status" in payload) {
    const env = payload as ApiEnvelope<T>;
    if (env.status === "error") {
      throw new ApiError(env.message ?? "业务失败", res.status, payload);
    }
    return (env.data ?? (env as unknown as T)) as T;
  }

  // 少数裸返回数组的接口（如 /content/{project_id}）
  return payload as T;
}

// -------------------------------------------------------------------------
// Projects
// -------------------------------------------------------------------------
export const projectsApi = {
  /**
   * 列项目。
   * `stack` 是可选的"启用栈"过滤：传 "api" 只返回 enabled_stacks 包含 "api" 的项目。
   * 历史 v1 这里叫 `type`（项目=单一栈），v2 起换成 `stack`，后端入口同步改名。
   */
  list(stack?: ProjectStack | string) {
    const qs = stack ? `?stack=${encodeURIComponent(stack)}` : "";
    return request<Project[]>(`/api/projects/list${qs}`);
  },
  get(id: number) {
    return request<Project>(`/api/projects/${id}`);
  },
  create(body: ProjectCreate) {
    return request<Project>("/api/projects", { method: "POST", body });
  },
  update(id: number, body: ProjectCreate) {
    return request<Project>(`/api/projects/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<void>(`/api/projects/${id}`, { method: "DELETE" });
  },
  /**
   * 项目详情页 Tab 角标用：每种 case_type 的用例数量 + 启用的栈集合。
   * 服务端会返回所有 case_type（含未启用的栈，可能 = 0），前端按 enabled_stacks 决定是否显示 Tab。
   */
  stackCounts(id: number) {
    return request<ProjectStackCounts>(`/api/projects/${id}/stack_counts`);
  },
  /** 读取项目 AI 概览（模块关联图谱）。 */
  getAiOverview(id: number) {
    return request<ProjectAiOverviewResp>(`/api/projects/${id}/ai_overview`);
  },
  /** （重新）生成项目 AI 概览，持久化到项目。 */
  genAiOverview(id: number, modelName: string) {
    return request<ProjectAiOverviewResp>(`/api/projects/${id}/ai_overview`, {
      method: "POST",
      body: { model_name: modelName },
    });
  },
};

// -------------------------------------------------------------------------
// Modules
// -------------------------------------------------------------------------
export const modulesApi = {
  get(id: number) {
    return request<Module>(`/api/modules/${id}`);
  },
  create(body: ModuleCreate) {
    return request<Module>("/api/modules", { method: "POST", body });
  },
  rename(id: number, name: string) {
    return request<void>(`/api/modules/${id}`, {
      method: "PUT",
      body: { name },
    });
  },
  remove(id: number) {
    return request<void>(`/api/modules/${id}`, { method: "DELETE" });
  },
  /**
   * 列出某项目下的"全部"模块（扁平），给"移动到…"的目录树挑选用。
   * 可选 excludeSubtree：把某个模块自身 + 后代排掉（防止自环）。
   */
  listForPicker(projectId: number, excludeSubtree?: number | null) {
    const params = new URLSearchParams({ project_id: String(projectId) });
    if (excludeSubtree != null) {
      params.set("exclude_subtree", String(excludeSubtree));
    }
    return request<ModulePickerNode[]>(`/api/modules?${params.toString()}`);
  },
  /**
   * 把模块挪到 targetParentId 下；targetParentId=null 即项目根。
   * 后端会做防环 + 同项目校验，并把目标父节点末尾的 sort_order+1 给到该模块。
   */
  move(id: number, targetParentId: number | null) {
    return request<{ id: number; parent_id: number | null; sort_order: number }>(
      `/api/modules/${id}/move`,
      {
        method: "PATCH",
        body: { target_parent_id: targetParentId },
      },
    );
  },
  /** 更新同级模块顺序（拖拽排序），使用已有 PATCH /api/reorder */
  reorder(items: { type: string; id: number; new_order: number }[]) {
    return request<{ status?: string }>("/api/reorder", {
      method: "PATCH",
      body: { items },
    });
  },
};

/** 移动对话框里挑目标父节点用的扁平节点。 */
export interface ModulePickerNode {
  id: number;
  name: string;
  parent_id: number | null;
  sort_order: number | null;
}

// -------------------------------------------------------------------------
// Content tree（模块 + 用例混合结构）
// -------------------------------------------------------------------------
export const contentApi = {
  /**
   * 列出一个 project 下 parent_id=X 的子节点（模块 + 用例）。
   *
   * `caseType`：可选，按 case_type 过滤用例（多值会拼成 "?case_type=api,mixed"）。
   * 注意：模块本身始终返回（栈无关），过滤只作用在用例上。
   * 例如项目详情页"API"Tab 通常传 ["api", "mixed"]，"功能"Tab 传 ["functional"]。
   */
  list(
    projectId: number,
    parentId?: number | null,
    caseType?: CaseType | CaseType[] | null,
  ) {
    const params = new URLSearchParams();
    if (parentId != null) params.set("parent_id", String(parentId));
    if (caseType) {
      const arr = Array.isArray(caseType) ? caseType : [caseType];
      const joined = arr.filter(Boolean).join(",");
      if (joined) params.set("case_type", joined);
    }
    const qs = params.toString();
    return request<ContentNode[]>(`/api/content/${projectId}${qs ? `?${qs}` : ""}`);
  },
};

// -------------------------------------------------------------------------
// Test Cases
// -------------------------------------------------------------------------
export const casesApi = {
  create(body: TestCaseCreate, sessionId?: string) {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return request<{ id: number }>(`/api/test_cases${q}`, {
      method: "POST",
      body,
    });
  },
  update(id: number, body: TestCaseCreate, sessionId?: string, historyBatchId?: number) {
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (historyBatchId != null) params.set("history_batch_id", String(historyBatchId));
    const qs = params.toString();
    return request<{ batch_id?: number | null }>(`/api/test_cases/${id}${qs ? `?${qs}` : ""}`, { method: "PUT", body });
  },
  /** 拉一个用例的详情（含 steps），给 Web/App 编辑态用。 */
  get(id: number) {
    return request<TestCaseDetail>(`/api/test_cases/${id}`);
  },
  remove(id: number, sessionId?: string, historyBatchId?: number) {
    const params = new URLSearchParams();
    if (sessionId) params.set("session_id", sessionId);
    if (historyBatchId != null) params.set("history_batch_id", String(historyBatchId));
    const qs = params.toString();
    return request<{ batch_id?: number | null }>(`/api/test_cases/${id}${qs ? `?${qs}` : ""}`, { method: "DELETE" });
  },
  rollbackHistory(batchId: number, payload: RequirementRollbackPayload) {
    return request<RequirementRollbackResult>(
      `/api/test_cases/edit-history/batches/${batchId}/rollback`,
      {
        method: "POST",
        body: payload,
      },
    );
  },
  /** 批量调整顺序：拖拽 / 插入时用。 */
  reorder(items: ReorderItem[]) {
    return request<void>("/api/reorder", {
      method: "PATCH",
      body: { items },
    });
  },
  /** Excel 批量导入。后端期望 `file` 字段 + `module_id` query 参数。 */
  importExcel(projectId: number, moduleId: number, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<void>(
      `/api/projects/${projectId}/import_cases?module_id=${moduleId}`,
      { method: "POST", body: form },
    );
  },
  /**
   * 用例导出：xlsx / csv。后端返回 attachment 流，这里走原生 fetch 直接拿 Blob，
   * 然后造一个临时 a 标签触发下载——绕开 react-query 的 JSON 反序列化路径。
   *
   * v2 起 module_id + case_type 都是后端必填字段：
   *   - moduleId：当前模块（含所有子模块按树前序遍历导出）
   *   - caseTypes：当前栈过滤，多值用逗号 join，如 ["api","mixed"] / ["functional"]
   * 文件名优先取 Content-Disposition 里的 filename*（RFC 5987），兜底用一个时间戳。
   */
  async exportCases(opts: {
    projectId: number;
    moduleId: number;
    caseTypes: CaseType[];
    format: "xlsx" | "csv";
  }) {
    const params = new URLSearchParams({ format: opts.format });
    params.set("module_id", String(opts.moduleId));
    params.set(
      "case_type",
      opts.caseTypes.filter(Boolean).join(",") || "api",
    );
    const url = `/api/projects/${opts.projectId}/export_cases?${params.toString()}`;
    const resp = await fetchWithAuth(url, { method: "GET" });
    if (!resp.ok) {
      // 错误响应是 JSON envelope，复用与 request() 类似的解析
      let detail = `导出失败 ${resp.status}`;
      try {
        const data = await resp.json();
        detail =
          (data && (data.detail || data.message)) ||
          (data && typeof data === "string" ? data : detail);
      } catch {
        /* 非 JSON 错误体直接用 status 文案 */
      }
      throw new ApiError(String(detail), resp.status);
    }
    const blob = await resp.blob();
    // 文件名解析：优先 filename*（UTF-8）；fallback 用拼接
    let fileName = `cases.${opts.format}`;
    const cd = resp.headers.get("Content-Disposition") || "";
    const m =
      /filename\*=UTF-8''([^;]+)/i.exec(cd) || /filename="?([^";]+)"?/i.exec(cd);
    if (m && m[1]) {
      try {
        fileName = decodeURIComponent(m[1]);
      } catch {
        fileName = m[1];
      }
    }
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  },
};

// -------------------------------------------------------------------------
// 自动化用例工作台（后端路径沿用 /api/api_cases，实际支持 api/web/android/ios）
// -------------------------------------------------------------------------
export const automationCasesApi = {
  list(filters: {
    moduleId: number;
    caseType?: CaseType;
    status?: ApiRunStatus | ApiRunStatus[];
    keyword?: string;
    flagType?: AiFlagType;
    manualAdjustment?: boolean;
    page?: number;
    pageSize?: number;
  }) {
    const qs = new URLSearchParams({ module_id: String(filters.moduleId) });
    qs.set("case_type", filters.caseType ?? "api");
    if (filters.status) {
      const values = Array.isArray(filters.status) ? filters.status : [filters.status];
      qs.set("status", values.join(","));
    }
    if (filters.keyword?.trim()) qs.set("keyword", filters.keyword.trim());
    if (filters.flagType) qs.set("flag_type", filters.flagType);
    if (filters.manualAdjustment) qs.set("manual_adjustment", "true");
    if (filters.page != null) qs.set("page", String(filters.page));
    if (filters.pageSize != null) qs.set("page_size", String(filters.pageSize));
    return request<ApiCaseListResponse>(`/api/api_cases?${qs}`);
  },
  /** 清除用例的 AI 诊断标记；reason 会作为反馈回流给下次 AI 诊断。 */
  clearAiFlag(caseId: number, body: { reason: AiFlagClearReason; corrected_classification?: string; note?: string }) {
    return request<AiCaseFlag>(`/api/api_cases/${caseId}/ai_flag/clear`, { method: "POST", body });
  },
  /** 用例的 AI 标记历史（含已清除的反馈记录）。 */
  aiFlagHistory(caseId: number, limit = 20) {
    return request<AiCaseFlag[]>(`/api/api_cases/${caseId}/ai_flags?limit=${limit}`);
  },
  /** 项目内各模块 active 标记计数（含子树聚合），模块卡片角标用。 */
  aiFlagCounts(projectId: number) {
    return request<AiFlagCounts>(`/api/api_cases/flag_counts?project_id=${projectId}`);
  },
  create(body: TestCaseCreate, sessionId?: string) {
    return casesApi.create({ ...body, case_type: body.case_type ?? "api" }, sessionId);
  },
  update(id: number, body: TestCaseCreate, sessionId?: string, historyBatchId?: number) {
    return casesApi.update(id, { ...body, case_type: body.case_type ?? "api" }, sessionId, historyBatchId);
  },
  remove(id: number, sessionId?: string, historyBatchId?: number) {
    return casesApi.remove(id, sessionId, historyBatchId);
  },
  /** 按执行顺序给模块下用例名加序号前缀 0001/0002/...（enable=false 去掉）。 */
  renumber(moduleId: number, opts?: { enable?: boolean; caseType?: string; width?: number }) {
    return request<{ total: number; updated: number }>(`/api/test_cases/renumber`, {
      method: "POST",
      body: {
        module_id: moduleId,
        enable: opts?.enable ?? true,
        case_type: opts?.caseType ?? "api",
        width: opts?.width ?? 4,
      },
    });
  },
  editHistory(moduleId: number, limit = 200, caseType: CaseType = "api") {
    return request<ApiCaseEditRecord[]>(
      `/api/api_cases/edit_history?module_id=${moduleId}&case_type=${caseType}&limit=${limit}`,
    );
  },
  testHistory(moduleId: number, limit = 100, caseType: CaseType = "api") {
    return request<ApiTestHistoryReport[]>(
      `/api/api_cases/test_history?module_id=${moduleId}&case_type=${caseType}&limit=${limit}`,
    );
  },
  runs(caseId: number, limit = 20) {
    return request<Array<{
      report_id: number;
      status: ApiRunStatus;
      executed_at: string | null;
      duration: number;
      error_message: string | null;
    }>>(`/api/api_cases/${caseId}/runs?limit=${limit}`);
  },
  latestRunDetail(caseId: number) {
    return request<ApiCaseLatestRunDetail>(`/api/api_cases/${caseId}/latest_run_detail`);
  },
};

/** @deprecated 请使用 automationCasesApi。保留旧名是为了兼容少量历史调用点。 */
export const apiCasesApi = automationCasesApi;

// -------------------------------------------------------------------------
// Functional Cases （人工功能用例 + "勾结果"链路）
// -------------------------------------------------------------------------

export interface FunctionalCaseListResponse {
  items: FunctionalCase[];
  total: number;
  page: number;
  page_size: number;
}

export interface FunctionalCaseListFilters {
  /** module_id 与 project_id 互斥；只能传一个。 */
  moduleId?: number;
  projectId?: number;
  /**
   * 按"最近一次执行状态"过滤；多值会拼成逗号分隔。
   * 包含 "pending" 表示"还没勾过的用例"也要纳入。
   */
  status?: FunctionalRunStatus | FunctionalRunStatus[];
  page?: number;
  pageSize?: number;
}

export interface FunctionalBatchMarkResult {
  batch_id: string;
  /** 成功创建的 run 数 */
  created: number;
  /** 单条 case 失败原因（partial-success：不会整批回滚） */
  errors: { case_id: number; error: string }[];
  items: { case_id: number; status: FunctionalRunStatus; batch_id: string }[];
}

export interface FunctionalImportResult {
  imported: number;
  errors: { row: number; error: string }[];
}

export const functionalCasesApi = {
  /** AI 接口用例质量：契约门禁、在线探测、首轮与最新真实执行通过率。 */
  aiGenerationQuality(projectId: number) {
    return request<{
      source: "ai_interface";
      total_cases: number;
      contract_bound: number;
      contract_rate: number | null;
      preflight_passed: number;
      preflight_rate: number | null;
      probe_attempted: number;
      probe_coverage_rate: number | null;
      probe_passed: number;
      probe_pass_rate: number | null;
      first_run_total: number;
      first_run_passed: number;
      first_run_pass_rate: number | null;
      latest_run_passed: number;
      latest_run_pass_rate: number | null;
      by_prompt_version: Record<string, Record<string, number>>;
    }>(`/api/functional_cases/ai_generation_quality?project_id=${projectId}`);
  },
  /** 创建一条功能用例（写入 test_cases，case_type='functional'）。sessionId=快速编辑会话 id。 */
  create(body: FunctionalCaseCreate, sessionId?: string) {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return request<FunctionalCase>(`/api/functional_cases${q}`, {
      method: "POST",
      body,
    });
  },
  /** 部分更新；后端用 exclude_unset 区分"没传 vs 传了 null"。 */
  update(id: number, body: FunctionalCaseUpdate, sessionId?: string) {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return request<FunctionalCase>(`/api/functional_cases/${id}${q}`, {
      method: "PUT",
      body,
    });
  },
  /** 删除（关联 FunctionalCaseRun 会随 cascade 一起删）。 */
  remove(id: number, sessionId?: string) {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return request<{ batch_id?: number | null }>(`/api/functional_cases/${id}${q}`, { method: "DELETE" });
  },
  /** 单条详情，含 latest_run。 */
  get(id: number) {
    return request<FunctionalCase>(`/api/functional_cases/${id}`);
  },
  /**
   * 列功能用例：可按 module 或 project 维度，附带最近一次"勾"。
   * 后端按"过滤后"的列表分页（带 status filter 时，total 是过滤后的总数）。
   */
  list(filters: FunctionalCaseListFilters = {}) {
    const qs = new URLSearchParams();
    if (filters.moduleId != null) qs.set("module_id", String(filters.moduleId));
    if (filters.projectId != null) qs.set("project_id", String(filters.projectId));
    if (filters.status) {
      const arr = Array.isArray(filters.status) ? filters.status : [filters.status];
      const joined = arr.filter(Boolean).join(",");
      if (joined) qs.set("status", joined);
    }
    if (filters.page != null) qs.set("page", String(filters.page));
    if (filters.pageSize != null) qs.set("page_size", String(filters.pageSize));
    const q = qs.toString();
    return request<FunctionalCaseListResponse>(
      `/api/functional_cases${q ? `?${q}` : ""}`,
    );
  },
  /** 单条勾结果。`batch_id` 可选，单点勾允许不传。 */
  mark(caseId: number, payload: FunctionalMarkPayload) {
    return request<FunctionalCaseRun>(`/api/functional_cases/${caseId}/mark`, {
      method: "POST",
      body: payload,
    });
  },
  /** 获取新 batch_id（测试模式用）。 */
  newBatchId() {
    return request<{ batch_id: string }>("/api/functional_cases/new_batch_id", {
      method: "POST",
    });
  },
  /** 批量勾结果。 */
  batchMark(payload: FunctionalBatchMarkPayload) {
    return request<FunctionalBatchMarkResult>(
      "/api/functional_cases/batch_mark",
      { method: "POST", body: payload },
    );
  },
  /** 用例历史执行记录。 */
  runs(caseId: number, limit: number = 20) {
    return request<FunctionalCaseRun[]>(
      `/api/functional_cases/${caseId}/runs?limit=${limit}`,
    );
  },
  /** 批次概览。 */
  batches(projectId: number, limit: number = 20) {
    return request<FunctionalBatchSummary[]>(
      `/api/functional_cases/batches?project_id=${projectId}&limit=${limit}`,
    );
  },
  /** 模块测试（勾结果）历史，带用例名（给"测试记录"按批次聚合）。 */
  testHistory(moduleId: number, limit: number = 300) {
    return request<FunctionalTestHistoryRun[]>(
      `/api/functional_cases/test_history?module_id=${moduleId}&limit=${limit}`,
    );
  },
  /** 模块编辑历史（新建/修改/删除）。 */
  editHistory(moduleId: number, limit: number = 100) {
    return request<FunctionalCaseEditRecord[]>(
      `/api/functional_cases/edit_history?module_id=${moduleId}&limit=${limit}`,
    );
  },
  /** 模块下每一次 AI 用例生成的数据库历史，只列大纲主记录。 */
  aiGenerationHistory(moduleId: number, mode: "functional" | "interface", limit = 50) {
    const qs = new URLSearchParams({
      module_id: String(moduleId),
      mode,
      limit: String(limit),
    });
    return request<AiGenerationHistoryItem[]>(
      `/api/functional_cases/ai_generation_history?${qs}`,
    );
  },
  /** 查看一次生成的完整大纲、详细用例和写入状态。 */
  aiGenerationHistoryDetail(runId: number, moduleId: number) {
    return request<AiGenerationHistoryDetail>(
      `/api/functional_cases/ai_generation_history/${runId}?module_id=${moduleId}`,
    );
  },
  /** 持久化生成向导审阅态；localStorage 仅保留为快速恢复副本。 */
  saveAiGenerationHistory(runId: number, moduleId: number, draft: Record<string, unknown>) {
    return request<AiGenerationHistoryItem>(
      `/api/functional_cases/ai_generation_history/${runId}`,
      { method: "PUT", body: { module_id: moduleId, draft } },
    );
  },
  /** AI 生成 第一步：文本 + 截图/原型图 + PDF/Word → 测试点大纲 + 需求摘要 digest。 */
  aiGenerateOutline(body: {
    module_id: number;
    text: string;
    model_name: string;
    mode?: "functional" | "interface";
    coverage?: "standard" | "full" | "exhaustive";
    doc_urls?: string;
    /** 接口模式可选：勾选的维度（逗号分隔），留空=按覆盖力度自动取舍全部。 */
    dimensions?: string;
    /** 前置链账号准备接口信息（用户直接粘贴，供前置链跨模块建账号用）。 */
    setup_doc?: string;
    images?: File[];
    docs?: File[];
  }, signal?: AbortSignal) {
    const fd = new FormData();
    fd.append("module_id", String(body.module_id));
    fd.append("model_name", body.model_name);
    fd.append("text", body.text);
    fd.append("mode", body.mode ?? "functional");
    fd.append("coverage", body.coverage ?? "standard");
    fd.append("doc_urls", body.doc_urls ?? "");
    fd.append("dimensions", body.dimensions ?? "");
    fd.append("setup_doc", body.setup_doc ?? "");
    (body.images ?? []).forEach((f) => fd.append("images", f));
    (body.docs ?? []).forEach((f) => fd.append("docs", f));
    return request<{
      digest: string;
      points: AiOutlinePoint[];
      model: string;
      image_strategy: string;
      api_contract: Record<string, unknown>;
      generation_run_id: number;
    }>("/api/functional_cases/ai_generate_outline", { method: "POST", body: fd, signal });
  },
  /** AI 生成 第二步：基于 digest + 本批测试点 + 已生成用例名 → 本批控件级详细用例。 */
  aiGenerateBatch(body: {
    module_id: number;
    model_name: string;
    digest: string;
    points: AiOutlinePoint[];
    done_names: string[];
    done_cases?: Array<Record<string, unknown>>;
    mode?: "functional" | "interface";
    /** 跨批次已产出的变量名（前端累积传入，避免误报"变量找不到来源"）。 */
    carried_vars?: string[];
    /** 前置链账号准备接口信息（供前置链跨模块建账号用）。 */
    setup_doc?: string;
    /** 原始 Swagger/OpenAPI 链接；服务端可在草稿契约丢失时重新解析。 */
    doc_urls?: string;
    api_contract?: Record<string, unknown>;
    generation_run_id?: number | null;
  }) {
    return request<{ cases: AiGeneratedCase[]; model: string; generation_run_id?: number | null }>(
      "/api/functional_cases/ai_generate_batch",
      { method: "POST", body },
    );
  },
  /** 高级补全：用 Codex / Claude Code CLI Agent 审稿并补全当前草稿。 */
  aiEnhanceCases(body: {
    module_id: number;
    agent_model_name: string;
    digest?: string;
    requirement_text?: string;
    cases: AiGeneratedCase[];
    mode?: "functional" | "interface";
    target_extra_count?: number;
    api_contract?: Record<string, unknown>;
    generation_run_id?: number | null;
  }) {
    return request<{
      cases: AiGeneratedCase[];
      summary: string;
      issues_found: string[];
      quality_score?: number | null;
      agent_model_name: string;
      run_id: number;
    }>("/api/functional_cases/ai_enhance_cases", {
      method: "POST",
      body,
    });
  },
  /** 不调用模型，按当前 OpenAPI 契约重新校验整批接口草稿。 */
  aiRevalidateCases(body: {
    module_id: number;
    cases: AiGeneratedCase[];
    api_contract: Record<string, unknown>;
    generation_run_id?: number | null;
  }) {
    return request<{
      cases: AiGeneratedCase[];
      total: number;
      writable: number;
      blocked: number;
    }>("/api/functional_cases/ai_revalidate_cases", {
      method: "POST",
      body,
    });
  },
  /** 分析一条接口用例最近一次执行结果：分类 + 原因 + 建议 + （用例问题时）修正。 */
  aiDiagnoseRun(body: { case_id: number; model_name: string }) {
    return request<{
      classification: string;
      reason: string;
      suggestion: string;
      fix: {
        extract: Record<string, unknown>;
        assertion: Record<string, unknown>;
        params?: Record<string, unknown>;
      };
    }>("/api/functional_cases/ai_diagnose_run", { method: "POST", body });
  },
  /** 提交报告级 AI 全面诊断 + 参数修复（异步）。返回 ai_run_id，前端轮询 /api/ai/runs/{id}
   *  拿 output_payload.items 再应用。任务会出现在全局任务看板，可终止。 */
  aiDiagnoseReport(body: { report_id: number; model_name: string }) {
    return request<{
      ai_run_id: number;
      feature: string;
      celery_task_id?: string;
    }>("/api/functional_cases/ai_diagnose_report", { method: "POST", body });
  },
  /** 应用报告级 AI 修复（服务端预检 + 快照 + 自动重跑验证；只有红转绿才保留）。
   *  闭环结果由后端写入 ai_run.output_payload.verify，轮询 /api/ai/runs/{id} 可见。 */
  aiApplyReportFixes(body: { ai_run_id: number; verify?: boolean }) {
    return request<{
      batch_id: number | null;
      applied: { case_id: number; name: string; event_id: number | null; parts: string[] }[];
      skipped: { case_id: number | null; name: string; reasons: string[] }[];
      verify_report_id: number | null;
    }>("/api/functional_cases/ai_report_fix/apply", { method: "POST", body });
  },
  /** 查漏补缺：给已有大纲找遗漏的测试点。text/doc_urls 传生成大纲时的原始材料，
   * 只靠 digest 模型拿不到字段级信息、找不出漏。 */
  aiOutlineGaps(body: {
    module_id: number;
    model_name: string;
    mode: "functional" | "interface";
    digest: string;
    points: AiOutlinePoint[];
    text?: string;
    doc_urls?: string;
    api_contract?: Record<string, unknown>;
  }) {
    return request<{ points: AiOutlinePoint[] }>("/api/functional_cases/ai_outline_gaps", {
      method: "POST",
      body,
    });
  },
  /** CLI Agent 查漏补缺：用 Codex CLI / Claude Code 审查当前大纲。 */
  aiOutlineGapsCli(body: {
    module_id: number;
    model_name: string;
    mode: "functional" | "interface";
    digest: string;
    points: AiOutlinePoint[];
    text?: string;
    doc_urls?: string;
    api_contract?: Record<string, unknown>;
  }) {
    return request<{ points: AiOutlinePoint[]; run_id: number }>(
      "/api/functional_cases/ai_outline_gaps_cli",
      { method: "POST", body },
    );
  },
  /** Excel 导入功能用例。 */
  importExcel(moduleId: number, file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<{ imported: number; errors: { row: number; error: string }[] }>(
      `/api/functional_cases/import?module_id=${moduleId}`,
      { method: "POST", body: form },
    );
  },
  /** Excel 导出功能用例。 */
  async exportExcel(opts: { projectId: number; moduleId?: number | null }) {
    const qs = new URLSearchParams({ project_id: String(opts.projectId) });
    if (opts.moduleId != null) qs.set("module_id", String(opts.moduleId));
    const url = `/api/functional_cases/export?${qs}`;
    const res = await fetchWithAuth(url);
    if (!res.ok) throw new ApiError("导出失败", res.status);
    const blob = await res.blob();
    const contentDisposition = res.headers.get("Content-Disposition") ?? "";
    const match = /filename\*=UTF-8''([^;]+)/.exec(contentDisposition);
    const filename = match ? decodeURIComponent(match[1]) : `export_${Date.now()}.xlsx`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  },
};

// -------------------------------------------------------------------------
// Test Run
// -------------------------------------------------------------------------
export const runsApi = {
  trigger(body: RunTestRequest) {
    return request<RunTestResult>("/api/run_test", { method: "POST", body });
  },
};

// -------------------------------------------------------------------------
// Config center
// -------------------------------------------------------------------------
export interface ConfigItem {
  id: number;
  config_group: string;
  config_key: string;
  config_value: string;
  category: string;
  project_id: number;
}

export interface ConfigSchemaItem {
  config_group: string;
  key: string;
  type: "str" | "bool" | "int" | "float" | "json";
  default: string;
  description: string;
  example: string;
  applies_to: string[];
}

export interface DatabaseConnectionOption {
  name: string;
  label: string;
  first_config_id: number;
}

export const configApi = {
  databaseConnections(projectId: number) {
    return request<DatabaseConnectionOption[]>(
      `/api/config/database-connections?project_id=${projectId}`,
    );
  },
  list(category: string | undefined, projectId: number) {
    const qs = new URLSearchParams();
    if (category) qs.set("category", category);
    qs.set("project_id", String(projectId));
    const q = qs.toString();
    return request<ConfigItem[]>(`/api/config/all${q ? `?${q}` : ""}`);
  },
  schema(category: string) {
    return request<ConfigSchemaItem[]>(
      `/api/config/schema/${encodeURIComponent(category)}`,
    );
  },
  save(body: Omit<ConfigItem, "id" | "project_id"> & { project_id: number }) {
    return request<void>("/api/config/save", { method: "POST", body });
  },
  add(body: Omit<ConfigItem, "id" | "project_id"> & { project_id: number }) {
    return request<void>("/api/config/add", { method: "POST", body });
  },
  remove(id: number) {
    return request<void>(`/api/config/delete/${id}`, { method: "DELETE" });
  },
  testAiModel(projectId: number, modelName: string) {
    return request<{ ok: boolean; result?: string; error?: string }>(
      "/api/config/test-ai-model",
      { method: "POST", body: { project_id: projectId, model_name: modelName } },
    );
  },
  testEmbedding(projectId: number) {
    return request<{ ok: boolean; result?: string; error?: string }>(
      "/api/config/test-embedding",
      { method: "POST", body: { project_id: projectId } },
    );
  },
};

// -------------------------------------------------------------------------
// Scripts（脚本库）
// -------------------------------------------------------------------------
export const scriptsApi = {
  list(params: { project_id?: number; scope?: ScriptScope | "available"; kind?: ScriptKind } = {}) {
    const qs = new URLSearchParams();
    if (params.project_id != null) qs.set("project_id", String(params.project_id));
    if (params.scope) qs.set("scope", params.scope);
    if (params.kind) qs.set("kind", params.kind);
    const query = qs.toString();
    return request<ScriptItem[]>(`/api/scripts${query ? `?${query}` : ""}`);
  },
  create(body: ScriptPayload) {
    return request<ScriptItem>("/api/scripts", { method: "POST", body });
  },
  update(id: number, body: ScriptPayload) {
    return request<ScriptItem>(`/api/scripts/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<void>(`/api/scripts/${id}`, { method: "DELETE" });
  },
  test(id: number, body: ScriptTestPayload) {
    return request<ScriptTestResult>(`/api/scripts/${id}/test`, { method: "POST", body });
  },
};

// -------------------------------------------------------------------------
// Reports （执行记录）
// -------------------------------------------------------------------------
export interface TestReportSummary {
  id: number;
  project_id: number | null;
  project_name?: string | null;
  category: string | null;
  scene_name: string | null;
  executor: string | null;
  status: string | null;
  total_count: number;
  pass_count: number;
  fail_count: number;
  error_count: number;
  skip_count: number;
  duration: number | null;
  summary: string | null;
  allure_url: string | null;
  context_session_id: number | null;
  start_time: string | null;
  end_time: string | null;
  create_time: string | null;
}

export interface TestStepReportItem {
  id: number;
  report_id: number;
  case_id: number | null;
  step_name: string | null;
  step_type: string | null;
  action: string | null;
  target: string | null;
  status: string | null;
  status_code: number | null;
  duration: number | null;
  error_message: string | null;
  context_session_id: number | null;
  context_event_from_seq: number | null;
  context_event_to_seq: number | null;
  create_time: string | null;
}

export interface TestReportDetail extends TestReportSummary {
  steps: TestStepReportItem[];
}

/** L1 确定性失败分诊：不调 LLM，按规则给失败用例定性（见 server/services/failure_triage.py）。 */
export interface ReportTriageCase {
  case_id: number;
  case_name: string | null;
  step_name: string | null;
  status_code: number | null;
  /** 用例问题 / 接口问题 / 环境或其他 / 待定 */
  classification: string;
  /** dangling_var / missing_auth / wrong_jsonpath / rate_limit / server_error … */
  subtype: string | null;
  summary: string;
  evidence: string;
  suggestion: string;
  /** 归因到上游失败用例时带上它们的 id */
  related_case_ids?: number[];
  /** 规则直接算出的修复（如正确的 JSONPath） */
  fix_hint?: Record<string, unknown>;
}

export interface ReportTriage {
  report_id: number;
  report_status: string | null;
  total_failed: number;
  triaged: number;
  undetermined: number;
  by_classification: Record<string, number>;
  cases: ReportTriageCase[];
}

export interface ReportAnalysisSuggestion {
  category: string;
  severity: string;
  confidence: number;
  case_id: number | null;
  step_report_id: number | null;
  step_id: number | null;
  step_name: string | null;
  title: string;
  evidence: string | null;
  action: Record<string, unknown>;
  apply_mode: "high_confidence" | "need_review" | "manual_required" | string;
}

export interface ReportAnalysisCase {
  case_id: number;
  module_id: number | null;
  name: string;
  case_type: string | null;
  status: string;
  classification: string;
  suggestions: ReportAnalysisSuggestion[];
}

export interface ReportAnalysisOutput {
  report_id: number;
  project_id: number | null;
  model_name?: string | null;
  rules_version: string;
  summary: {
    total_cases: number;
    total_suggestions: number;
    by_category: Record<string, number>;
    by_severity: Record<string, number>;
    high_confidence?: number;
    need_review?: number;
    manual_required?: number;
    message?: string;
  };
  cases: ReportAnalysisCase[];
  ai_summary?: string | null;
  ai_error?: string | null;
}

export interface ReportsListResponse {
  data: TestReportSummary[];
  total: number;
}

/**
 * /api/reports 返回信封是 `{status, data, total}`（total 在顶层）。
 * lib/api 的 request<T> 默认剥 `data` —— 这里要总数也拿到，手写一个 wrapper。
 */
async function fetchReports(params: {
  project_id?: number;
  category?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<ReportsListResponse> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  });
  const path = `/api/reports${qs.toString() ? "?" + qs.toString() : ""}`;
  const headers = new Headers({ Accept: "application/json" });
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const res = await fetch(path, { headers });
  const payload = (await res.json()) as {
    status: string;
    data: TestReportSummary[];
    total: number;
    message?: string;
  };
  if (!res.ok || payload.status === "error") {
    throw new ApiError(
      payload.message ?? `HTTP ${res.status}`,
      res.status,
      payload,
    );
  }
  return { data: payload.data, total: payload.total };
}

export const reportsApi = {
  list: fetchReports,
  get(id: number) {
    return request<TestReportDetail>(`/api/reports/${id}`);
  },
  analysisPreview(id: number, signal?: AbortSignal) {
    return request<ReportAnalysisOutput>(`/api/reports/${id}/analysis-preview`, { signal });
  },
  /** L1 确定性失败分诊（零 LLM 成本，报告一打开就能看）。 */
  triage(id: number) {
    return request<ReportTriage>(`/api/reports/${id}/triage`);
  },
  analyze(id: number, body: { model_name?: string | null; operator?: string | null } = {}) {
    return request<AiSubmitResponse>(`/api/reports/${id}/ai-analysis`, {
      method: "POST",
      body,
    });
  },
  /** 取该报告最近一次成功的 AI 全面分析结果（复用上次、避免重跑）；无则 data=null。 */
  analysisLatest(id: number) {
    return request<{
      ai_run_id: number;
      status: string;
      output_payload: ReportAnalysisOutput | null;
      created_at: string | null;
    } | null>(`/api/reports/${id}/ai-analysis/latest`);
  },
  remove(id: number) {
    return request<void>(`/api/reports/${id}`, { method: "DELETE" });
  },
};

// -------------------------------------------------------------------------
// System / Service Status —— 工作台健康检查
// -------------------------------------------------------------------------
export type ServiceHealth = "up" | "down" | "unknown";
export type OverallHealth = "healthy" | "degraded" | "down";

export interface ServiceStatusItem {
  key: string;
  name: string;
  required: boolean;
  status: ServiceHealth;
  detail: string;
  latency_ms: number | null;
}

export interface SystemServicesStatus {
  overall: OverallHealth;
  checked_at: string;
  hostname: string;
  pid: number;
  services: ServiceStatusItem[];
}

export const systemApi = {
  services() {
    return request<SystemServicesStatus>("/api/system/services");
  },
};

// -------------------------------------------------------------------------
// Devices （设备池）
// -------------------------------------------------------------------------
export type DeviceStatus = "idle" | "busy" | "offline";
export type DevicePlatform = "Android" | "iOS";

export interface Device {
  id: number;
  udid: string;
  platform: DevicePlatform;
  platform_version?: string | null;
  device_name?: string | null;
  brand?: string | null;
  model?: string | null;
  agent_host?: string | null;
  agent_port?: number | null;
  appium_port?: number | null;
  pool: string;
  status: DeviceStatus;
  owner_execution_id?: number | null;
  capabilities?: Record<string, unknown> | null;
  tags?: string[] | null;
  last_heartbeat?: string | null;
  /** 后端 probe_devices 任务累计失败次数；>=2 会把 status 置 offline。 */
  consecutive_failures?: number;
}

export interface DeviceUpsertBody {
  udid: string;
  platform: string;
  platform_version?: string;
  device_name?: string;
  brand?: string;
  model?: string;
  agent_host?: string;
  agent_port?: number;
  appium_port?: number;
  pool?: string;
  status?: DeviceStatus;
  capabilities?: Record<string, unknown>;
  tags?: string[];
}

export interface DeviceListFilters {
  pool?: string;
  platform?: string;
  status?: DeviceStatus;
}

export const devicesApi = {
  list(filters: DeviceListFilters = {}) {
    const qs = new URLSearchParams();
    if (filters.pool) qs.set("pool", filters.pool);
    if (filters.platform) qs.set("platform", filters.platform);
    if (filters.status) qs.set("status", filters.status);
    const q = qs.toString();
    return request<Device[]>(`/api/devices/list${q ? `?${q}` : ""}`);
  },
  get(id: number) {
    return request<Device>(`/api/devices/${id}`);
  },
  pools() {
    return request<string[]>("/api/devices/pools");
  },
  create(body: DeviceUpsertBody) {
    return request<Device>("/api/devices", { method: "POST", body });
  },
  update(id: number, body: DeviceUpsertBody) {
    return request<Device>(`/api/devices/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<void>(`/api/devices/${id}`, { method: "DELETE" });
  },
  release(id: number) {
    return request<void>(`/api/devices/release/${id}`, { method: "POST" });
  },
};

// ============================================================
// App 安装包仓库（apk / ipa 上传，让 step 编辑器从下拉里挑包）
// ============================================================
export interface AppPackage {
  id: number;
  name: string;
  file_name: string;
  file_path: string;
  platform: "android" | "ios";
  app_package?: string | null;
  bundle_id?: string | null;
  version?: string | null;
  file_size: number;
  project_id?: number | null;
  description?: string | null;
  upload_time?: string | null;
}

export interface AppPackageUploadFields {
  name: string;
  platform?: "android" | "ios";
  app_package?: string;
  bundle_id?: string;
  version?: string;
  project_id?: number;
  description?: string;
}

export interface AppPackageListFilters {
  platform?: "android" | "ios";
  project_id?: number;
}

export const appPackagesApi = {
  list(filters: AppPackageListFilters = {}) {
    const qs = new URLSearchParams();
    if (filters.platform) qs.set("platform", filters.platform);
    if (filters.project_id != null) qs.set("project_id", String(filters.project_id));
    const q = qs.toString();
    return request<AppPackage[]>(`/api/app_packages${q ? `?${q}` : ""}`);
  },
  get(id: number) {
    return request<AppPackage>(`/api/app_packages/${id}`);
  },
  upload(file: File, fields: AppPackageUploadFields) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", fields.name);
    if (fields.platform) fd.append("platform", fields.platform);
    if (fields.app_package) fd.append("app_package", fields.app_package);
    if (fields.bundle_id) fd.append("bundle_id", fields.bundle_id);
    if (fields.version) fd.append("version", fields.version);
    if (fields.project_id != null) fd.append("project_id", String(fields.project_id));
    if (fields.description) fd.append("description", fields.description);
    return request<AppPackage>("/api/app_packages", { method: "POST", body: fd });
  },
  remove(id: number) {
    return request<void>(`/api/app_packages/${id}`, { method: "DELETE" });
  },
  /** 直接给 <a href> 用的下载地址。 */
  downloadUrl(id: number) {
    return `/api/app_packages/${id}/download`;
  },
};


// =============================================================================
// AI 任务（Phase A：requirement_parse + 通用查询/取消）
// =============================================================================

/** 提交 AI 任务返回的形状。 */
export interface AiSubmitResponse {
  ai_run_id: number;
  celery_task_id: string;
  feature: string;
}

export const aiApi = {
  /** AI 需求分析：提交文本或文件路径 + 分析模式，异步生成 requirements。 */
  submitRequirementParse(payload: {
    project_id: number;
    text?: string;
    file_path?: string;
    analysis_mode?: string;
    operator?: string;
  }) {
    return request<AiSubmitResponse>("/api/ai/requirement_parse", {
      method: "POST",
      body: payload,
    });
  },
  /** 查单个 ai_run（轮询用）。 */
  getRun(id: number) {
    return request<AiRun>(`/api/ai/runs/${id}`);
  },
  /** 列 ai_run 历史。 */
  listRuns(filters: {
    project_id?: number;
    feature?: AiFeature | string;
    status?: AiRunStatus;
    limit?: number;
  } = {}) {
    const qs = new URLSearchParams();
    if (filters.project_id != null) qs.set("project_id", String(filters.project_id));
    if (filters.feature) qs.set("feature", filters.feature);
    if (filters.status) qs.set("status", filters.status);
    if (filters.limit != null) qs.set("limit", String(filters.limit));
    const q = qs.toString();
    return request<AiRun[]>(`/api/ai/runs${q ? `?${q}` : ""}`);
  },
  /** 取消任务（revoke Celery + 改 status=cancelled）。 */
  cancelRun(id: number) {
    return request<{ message?: string }>(`/api/ai/runs/${id}/cancel`, {
      method: "POST",
    });
  },
};


// =============================================================================
// 需求点（requirements）—— 项目下的需求点 CRUD
// =============================================================================

export const requirementsApi = {
  list(projectId: number, filters: RequirementListFilters = {}) {
    const qs = new URLSearchParams({ project_id: String(projectId) });
    if (filters.status) qs.set("status", filters.status);
    if (filters.source) qs.set("source", filters.source);
    if (filters.version_id !== undefined)
      qs.set("version_id", String(filters.version_id));
    if (filters.system_status) qs.set("system_status", filters.system_status);
    if (filters.business_status) qs.set("business_status", filters.business_status);
    if (filters.assignee_pm_id !== undefined)
      qs.set("assignee_pm_id", String(filters.assignee_pm_id));
    if (filters.module_id !== undefined)
      qs.set("module_id", String(filters.module_id));
    if (filters.tree) qs.set("tree", "true");
    return request<Requirement[]>(`/api/requirements?${qs.toString()}`);
  },
  get(id: number) {
    return request<Requirement>(`/api/requirements/${id}`);
  },
  coverage(projectId: number) {
    return request<CoverageResult>(`/api/requirements/coverage?project_id=${projectId}`);
  },
  create(payload: RequirementCreate) {
    return request<Requirement>("/api/requirements", {
      method: "POST",
      body: payload,
    });
  },
  update(id: number, payload: RequirementUpdate) {
    return request<Requirement>(`/api/requirements/${id}`, {
      method: "PUT",
      body: payload,
    });
  },
  remove(id: number) {
    return request<{ deleted_ids: number[]; batch_id?: number }>(`/api/requirements/${id}`, {
      method: "DELETE",
    });
  },
  /** M5：拆子需求，传入 N 个子需求规格，返回创建好的子需求列表。 */
  split(id: number, items: RequirementSplitItem[]) {
    return request<Requirement[]>(`/api/requirements/${id}/split`, {
      method: "POST",
      body: items,
    });
  },
  /** PM 一键验收：要求 system_status='ready_to_release'，否则后端返 409。 */
  accept(id: number, payload: RequirementAcceptPayload = {}) {
    return request<Requirement>(`/api/requirements/${id}/accept`, {
      method: "POST",
      body: payload,
    });
  },
  /** M6：查询需求编辑历史 */
  getHistory(id: number) {
    return request<RequirementEditHistory[]>(`/api/requirements/${id}/history`);
  },
  rollbackHistory(batchId: number, payload: RequirementRollbackPayload) {
    return request<RequirementRollbackResult>(
      `/api/requirements/history/batches/${batchId}/rollback`,
      {
        method: "POST",
        body: payload,
      },
    );
  },
};


// =============================================================================
// 附件（M5）—— 需求的外链 / 上传文件
// =============================================================================

export const attachmentsApi = {
  list(requirementId: number) {
    return request<Attachment[]>(
      `/api/requirements/${requirementId}/attachments`,
    );
  },
  createLink(
    requirementId: number,
    payload: { name: string; url: string; uploaded_by_id?: number },
  ) {
    const fd = new FormData();
    fd.append("kind", "link");
    fd.append("name", payload.name);
    fd.append("url", payload.url);
    if (payload.uploaded_by_id !== undefined) {
      fd.append("uploaded_by_id", String(payload.uploaded_by_id));
    }
    return request<Attachment>(`/api/requirements/${requirementId}/attachments`, {
      method: "POST",
      body: fd,
    });
  },
  uploadFile(
    requirementId: number,
    file: File,
    extras: { name?: string; uploaded_by_id?: number } = {},
  ) {
    const fd = new FormData();
    fd.append("kind", "file");
    fd.append("file", file);
    if (extras.name) fd.append("name", extras.name);
    if (extras.uploaded_by_id !== undefined) {
      fd.append("uploaded_by_id", String(extras.uploaded_by_id));
    }
    return request<Attachment>(`/api/requirements/${requirementId}/attachments`, {
      method: "POST",
      body: fd,
    });
  },
  remove(attachmentId: number) {
    return request<void>(`/api/attachments/${attachmentId}`, { method: "DELETE" });
  },
};


// =============================================================================
// 版本迭代（project_versions）
// =============================================================================

export const versionsApi = {
  /** 全局版本选择器（供 bug 创建弹窗等场景使用）。 */
  picker() {
    return request<VersionPickerItem[]>("/api/versions/picker");
  },
  list(projectId: number) {
    return request<ProjectVersion[]>(`/api/projects/${projectId}/versions`);
  },
  get(projectId: number, versionId: number) {
    return request<ProjectVersion>(`/api/projects/${projectId}/versions/${versionId}`);
  },
  create(projectId: number, payload: VersionCreate) {
    return request<ProjectVersion>(`/api/projects/${projectId}/versions`, {
      method: "POST",
      body: payload,
    });
  },
  update(projectId: number, versionId: number, payload: VersionUpdate) {
    return request<ProjectVersion>(`/api/projects/${projectId}/versions/${versionId}`, {
      method: "PUT",
      body: payload,
    });
  },
  remove(projectId: number, versionId: number) {
    return request<void>(`/api/projects/${projectId}/versions/${versionId}`, { method: "DELETE" });
  },
  updateModules(projectId: number, versionId: number, moduleIds: number[]) {
    return request<ProjectVersion>(`/api/projects/${projectId}/versions/${versionId}/modules`, {
      method: "PUT",
      body: { module_ids: moduleIds },
    });
  },
  /** 版本看板：requirements_by_status 4 桶 + task_counts_by_type 计数。
   *  注意路径用的是 /api/project-versions（带连字符），跟 versions CRUD 走的
   *  /api/projects/:pid/versions/:vid 不是同一前缀。 */
  board(versionId: number) {
    return request<VersionBoard>(`/api/project-versions/${versionId}/board`);
  },
  /** 按版本列绑定的自动化用例 + 每条最近一次执行状态。M4 CasesTab 用。 */
  listCases(
    versionId: number,
    params?: { module_id?: number; case_type?: CaseType; status?: string },
  ) {
    const qs = new URLSearchParams();
    if (params?.module_id !== undefined) qs.set("module_id", String(params.module_id));
    if (params?.case_type) qs.set("case_type", params.case_type);
    if (params?.status) qs.set("status", params.status);
    const search = qs.toString();
    return request<{ items: VersionCase[]; total: number }>(
      `/api/project-versions/${versionId}/cases${search ? `?${search}` : ""}`,
    );
  },
};

// =============================================================================
// 用户 / 角色（PM 重设计 M3）
// =============================================================================

export const usersApi = {
  list(filters: { is_active?: boolean; role_code?: string; q?: string } = {}) {
    const qs = new URLSearchParams();
    if (filters.is_active !== undefined)
      qs.set("is_active", String(filters.is_active));
    if (filters.role_code) qs.set("role_code", filters.role_code);
    if (filters.q) qs.set("q", filters.q);
    const search = qs.toString();
    return request<User[]>(`/api/users${search ? `?${search}` : ""}`);
  },
  get(id: number) {
    return request<User>(`/api/users/${id}`);
  },
  create(payload: UserCreate) {
    return request<User>("/api/users", { method: "POST", body: payload });
  },
  update(id: number, payload: UserUpdate) {
    return request<User>(`/api/users/${id}`, { method: "PUT", body: payload });
  },
  /** 软删除：后端把 is_active 置 false，不真删。 */
  remove(id: number) {
    return request<void>(`/api/users/${id}`, { method: "DELETE" });
  },
  /** 全量替换角色（POST /:id/roles，body: { role_codes: [...] }）。 */
  setRoles(id: number, roleCodes: string[]) {
    return request<User>(`/api/users/${id}/roles`, {
      method: "POST",
      body: { role_codes: roleCodes },
    });
  },
  /** 单角色摘除（DELETE /:id/roles/:role_code）。 */
  removeRole(id: number, roleCode: string) {
    return request<User>(`/api/users/${id}/roles/${roleCode}`, {
      method: "DELETE",
    });
  },
};

export const authApi = {
  login(payload: LoginRequest) {
    return request<LoginResponse>("/api/auth/login", {
      method: "POST",
      body: { ...payload, client: payload.client ?? buildClientInfo() },
    });
  },
  me() {
    return request<User>("/api/auth/me");
  },
  sessions() {
    return request<UserSession[]>("/api/auth/sessions");
  },
  refresh(refreshToken: string) {
    return request<{ access_token: string; expires_in: number }>(
      "/api/auth/refresh",
      {
        method: "POST",
        body: { refresh_token: refreshToken },
      },
      false,
    );
  },
  logout(refreshToken?: string | null) {
    return request<void>(
      "/api/auth/logout",
      {
        method: "POST",
        body: { refresh_token: refreshToken ?? getRefreshToken() },
      },
      false,
    );
  },
  logoutAll() {
    return request<{ revoked: number }>("/api/auth/logout-all", {
      method: "POST",
    });
  },
  changePassword(payload: ChangePasswordRequest) {
    return request<{ message: string }>("/api/auth/password", {
      method: "PUT",
      body: payload,
    });
  },
};

export const rolesApi = {
  list() {
    return request<Role[]>("/api/roles");
  },
};

// =============================================================================
// 任务（Task）—— PM 重设计 M3 核心数据源
// =============================================================================

export const tasksApi = {
  list(filters: TaskListFilters = {}) {
    const qs = new URLSearchParams();
    if (filters.requirement_id !== undefined)
      qs.set("requirement_id", String(filters.requirement_id));
    if (filters.assignee_dev_id !== undefined)
      qs.set("assignee_dev_id", String(filters.assignee_dev_id));
    if (filters.assignee_test_id !== undefined)
      qs.set("assignee_test_id", String(filters.assignee_test_id));
    if (filters.type) qs.set("type", filters.type);
    if (filters.status) qs.set("status", filters.status);
    if (filters.created_by_id !== undefined)
      qs.set("created_by_id", String(filters.created_by_id));
    if (filters.closed_at_after)
      qs.set("closed_at_after", filters.closed_at_after);
    if (filters.parent_task_id !== undefined)
      qs.set("parent_task_id", String(filters.parent_task_id));
    if (filters.ids?.length) qs.set("ids", filters.ids.join(","));
    if (filters.version_id !== undefined)
      qs.set("version_id", String(filters.version_id));
    const search = qs.toString();
    return request<Task[]>(`/api/tasks${search ? `?${search}` : ""}`);
  },
  get(id: number) {
    return request<Task>(`/api/tasks/${id}`);
  },
  create(payload: TaskCreate) {
    return request<Task>("/api/tasks", { method: "POST", body: payload });
  },
  update(id: number, payload: TaskUpdate) {
    return request<Task>(`/api/tasks/${id}`, { method: "PUT", body: payload });
  },
  remove(id: number) {
    return request<void>(`/api/tasks/${id}`, { method: "DELETE" });
  },
  /** 测试报告快捷建 bug：自动设 type=bug、status=dev_doing、
   *  从 parent_task 继承 assignee_dev_id / requirement_id。 */
  fromTestFailure(payload: TaskFromTestFailurePayload) {
    return request<Task>("/api/tasks/from-test-failure", {
      method: "POST",
      body: payload,
    });
  },
};

// =============================================================================
// AI Bug Fix —— 智能体修复 Bug
// =============================================================================

export const bugFixApi = {
  /** 获取可用智能体列表。 */
  listAgents() {
    return request<{ agents: BugFixAgent[] }>("/api/ai/bug-fix-agents");
  },
  /** 触发 AI 修复 Bug。 */
  fixBug(taskId: number, agentName: string) {
    return request<AiBugFixResponse>(`/api/tasks/${taskId}/ai-fix`, {
      method: "POST",
      body: { agent_name: agentName },
    });
  },
  /** 回滚 AI 修复。 */
  rollback(taskId: number, aiRunId: number) {
    return request<{ message: string }>(`/api/tasks/${taskId}/ai-fix/rollback`, {
      method: "POST",
      body: { ai_run_id: aiRunId },
    });
  },
};

// =============================================================================
// 版本测试汇总（按需生成 + 强制重算）
// =============================================================================

// =============================================================================
// M6：AI 模型配置 CRUD
// =============================================================================

export const aiModelsApi = {
  list(projectId: number) {
    return request<AiModelConfig[]>(`/api/ai-models?project_id=${projectId}`);
  },
  create(payload: AiModelConfig) {
    return request<AiModelConfig>("/api/ai-models", {
      method: "POST",
      body: payload,
    });
  },
  update(name: string, payload: AiModelConfigUpsert) {
    return request<AiModelConfig>(`/api/ai-models/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: payload,
    });
  },
  remove(name: string) {
    return request<{ deleted: number }>(
      `/api/ai-models/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    );
  },
  test(name: string) {
    return request<AiModelTestResult>(
      `/api/ai-models/${encodeURIComponent(name)}/test`,
      { method: "POST" },
    );
  },
};


// =============================================================================
// M6：AI 需求分析文档 + 版本历史
// =============================================================================

export const analysisDocsApi = {
  listByRequirement(rid: number) {
    return request<AnalysisDocument[]>(
      `/api/requirements/${rid}/analysis-documents`,
    );
  },
  trigger(
    rid: number,
    body: {
      model_names: string[];
      user_prompt?: string;
      document_title?: string;
      analysis_type?: string;
    },
  ) {
    return request<AnalysisTriggerResponse>(
      `/api/requirements/${rid}/analysis-documents`,
      { method: "POST", body },
    );
  },
  get(id: number) {
    return request<AnalysisDocument>(`/api/analysis-documents/${id}`);
  },
  save(
    id: number,
    body: { markdown: string; change_summary?: string; title?: string },
  ) {
    return request<{ document: AnalysisDocument; new_version_no: number }>(
      `/api/analysis-documents/${id}`,
      { method: "PUT", body },
    );
  },
  remove(id: number) {
    return request<{ deleted: number }>(`/api/analysis-documents/${id}`, {
      method: "DELETE",
    });
  },
  listVersions(id: number) {
    return request<AnalysisVersion[]>(`/api/analysis-documents/${id}/versions`);
  },
  getVersion(id: number, versionNo: number) {
    return request<AnalysisVersion>(
      `/api/analysis-documents/${id}/versions/${versionNo}`,
    );
  },
  getDiff(id: number, a: number, b: number) {
    return request<AnalysisDiffResponse>(
      `/api/analysis-documents/${id}/versions/${a}/diff/${b}`,
    );
  },
  /** 浏览器触发 .md 下载。返回 void —— 在调用方包 try/catch。 */
  async export(id: number, fallbackTitle = `analysis_${id}`) {
    const token = getToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`/api/analysis-documents/${id}/export`, { headers });
    if (!res.ok) {
      throw new ApiError(`导出失败 HTTP ${res.status}`, res.status);
    }
    const blob = await res.blob();
    // 解 Content-Disposition 拿文件名（兼容 filename*=UTF-8''xxx）
    const cd = res.headers.get("Content-Disposition") || "";
    let filename = `${fallbackTitle}.md`;
    const m1 = cd.match(/filename\*=UTF-8''([^;]+)/i);
    const m2 = cd.match(/filename="?([^";]+)"?/i);
    if (m1) filename = decodeURIComponent(m1[1]);
    else if (m2) filename = m2[1];

    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
  },
};


// =============================================================================
// M7: AI 一键生成测试用例
// =============================================================================

export const aiCaseGenerationApi = {
  /** 触发：requirement_ids × model_names 笛卡尔积 → 每对一个 batch。 */
  trigger(payload: CaseGenerationTriggerPayload) {
    return request<CaseGenerationTriggerResponse>("/api/ai/case-generation", {
      method: "POST",
      body: payload,
    });
  },
  /** 列草稿：requirement_id / batch_id / status 任意组合过滤。 */
  listDrafts(filters: {
    requirement_id?: number;
    batch_id?: string;
    status?: AiCaseDraftStatus;
  } = {}) {
    const qs = new URLSearchParams();
    if (filters.requirement_id != null)
      qs.set("requirement_id", String(filters.requirement_id));
    if (filters.batch_id) qs.set("batch_id", filters.batch_id);
    if (filters.status) qs.set("status", filters.status);
    const q = qs.toString();
    return request<AiCaseDraft[]>(`/api/ai/case-drafts${q ? `?${q}` : ""}`);
  },
  getDraft(id: number) {
    return request<AiCaseDraft>(`/api/ai/case-drafts/${id}`);
  },
  updateDraft(id: number, patch: AiCaseDraftUpdatePayload) {
    return request<AiCaseDraft>(`/api/ai/case-drafts/${id}`, {
      method: "PUT",
      body: patch,
    });
  },
  /** 逻辑删：status → rejected。reason 是数据飞轮信号（回填 prompt 反例 + 统计），尽量填。 */
  rejectDraft(id: number, reason?: string) {
    const qs = reason?.trim()
      ? `?reason=${encodeURIComponent(reason.trim().slice(0, 500))}`
      : "";
    return request<AiCaseDraft>(`/api/ai/case-drafts/${id}${qs}`, {
      method: "DELETE",
    });
  },
  /** 批量入库：勾选若干草稿 → 写 test_cases；不通过的进 skipped[]。 */
  commit(payload: CommitDraftsPayload) {
    return request<CommitDraftsResult>("/api/ai/case-drafts/commit", {
      method: "POST",
      body: payload,
    });
  },
  /** 查任务进度（专用端点，扁平化了 batch_id / requirement_id）。 */
  getRun(id: number) {
    return request<CaseGenerationRun>(`/api/ai/case-generation/runs/${id}`);
  },
};


export const versionSummariesApi = {
  /** 首次 GET 实时算 + 落库；后续 GET 直读缓存。 */
  get(versionId: number) {
    return request<VersionTestSummary>(`/api/version-summaries/${versionId}`);
  },
  regenerate(versionId: number) {
    return request<VersionTestSummary>(
      `/api/version-summaries/${versionId}/regenerate`,
      { method: "POST" },
    );
  },
};

// =============================================================================
// 全局任务看板 —— GET /api/tasks/in-progress
// =============================================================================

export const tasksOverviewApi = {
  /** 获取所有进行中的异步任务（AI + 执行 + 系统）。 */
  getInProgress(projectId?: number) {
    const qs = new URLSearchParams();
    if (projectId != null) qs.set("project_id", String(projectId));
    const q = qs.toString();
    return request<import("@/types/domain").InProgressTask[]>(
      `/api/tasks-overview/in-progress${q ? `?${q}` : ""}`,
    );
  },
  /** 从任务看板终止进行中的任务。 */
  cancelTask(typeKey: string, id: number) {
    return request<{ message?: string }>(
      `/api/tasks-overview/${encodeURIComponent(typeKey)}/${id}/cancel`,
      { method: "POST" },
    );
  },
};

// =============================================================================
// 变更调整（change-adjust）—— 需求变更驱动的接口用例增改删
// =============================================================================

export const changeAdjustApi = {
  /**
   * 预览：变更文本 + 可选链接 / 附件 → AI 生成的增改删操作计划（不落库）。
   * multipart 表单，走 request<T> 自带的 FormData 分支（不手动设 Content-Type，
   * 由浏览器补齐 boundary），与 functionalCasesApi.aiGenerateOutline 同一套路。
   */
  preview(args: {
    moduleId: number;
    changeText: string;
    modelName: string;
    links: string;
    files: File[];
  }) {
    const fd = new FormData();
    fd.append("module_id", String(args.moduleId));
    fd.append("change_text", args.changeText);
    fd.append("model_name", args.modelName);
    fd.append("links", args.links);
    args.files.forEach((f) => fd.append("files", f));
    return request<ChangePlan>("/api/change_plan/preview", {
      method: "POST",
      body: fd,
    });
  },
  /** 应用：勾选的 op + 需二次确认的删除 op → 实际写入 test_cases，返回增/改/删汇总。 */
  apply(args: {
    planId: number;
    selectedOpIds: number[];
    confirmedDeleteIds: number[];
  }) {
    return request<ApplySummary>("/api/change_plan/apply", {
      method: "POST",
      body: {
        plan_id: args.planId,
        selected_op_ids: args.selectedOpIds,
        confirmed_delete_ids: args.confirmedDeleteIds,
      },
    });
  },
};

// =============================================================================
// UI 录制中心与可视化元素库
// =============================================================================

export interface UiMobilePreflight {
  ready: boolean;
  tools: Record<string, string | null>;
  appium: {
    installed: boolean;
    running: boolean;
    url: string;
    version?: string | null;
    reason?: string;
  };
  drivers: Record<string, { installed: boolean; version?: string; install_path?: string }>;
  android_devices: Array<{ udid: string; state: string; description: string }>;
  ios_devices: Array<{ udid: string; state: string; description: string }>;
  ios_issues: string[];
  platform_ready: { android: boolean; ios: boolean };
}

export const uiRecordingsApi = {
  mobilePreflight() {
    return request<UiMobilePreflight>("/api/ui-recordings/mobile-preflight");
  },
  list(projectId: number, platform?: UiPlatform) {
    const qs = new URLSearchParams({ project_id: String(projectId) });
    if (platform) qs.set("platform", platform);
    return request<UiRecordingSession[]>(`/api/ui-recordings?${qs.toString()}`);
  },
  get(sessionId: number) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}`);
  },
  create(payload: {
    project_id: number;
    platform: UiPlatform;
    name: string;
    source_url?: string;
    device_id?: number;
    app_package_id?: number;
    capture_config?: Record<string, unknown>;
    recording_role?: "auto" | "primary" | "supplement" | "history";
    baseline_session_id?: number;
  }) {
    return request<UiRecordingSession>("/api/ui-recordings", {
      method: "POST",
      body: payload,
    });
  },
  updateBaseline(sessionId: number, action: "include" | "exclude" | "promote") {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/baseline`, {
      method: "POST",
      body: { action },
    });
  },
  control(
    sessionId: number,
    action: "start" | "pause" | "resume" | "stop" | "cancel",
    control?: {
      client_instance_id: string;
      command_id: string;
      takeover?: boolean;
    },
  ) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/${action}`, {
      method: "POST",
      body: control,
    });
  },
  updateLease(
    sessionId: number,
    payload: {
      client_instance_id: string;
      action: "claim" | "heartbeat" | "takeover" | "release";
    },
  ) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/control-lease`, {
      method: "POST",
      body: payload,
    });
  },
  setPickMode(
    sessionId: number,
    payload: { client_instance_id: string; command_id: string; enabled: boolean },
  ) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/pick-mode`, {
      method: "POST",
      body: payload,
    });
  },
  startExploration(
    sessionId: number,
    payload: {
      client_instance_id: string;
      command_id: string;
      max_pages?: number;
      max_depth?: number;
      max_actions_per_page?: number;
      timeout_seconds?: number;
      login_wait_seconds?: number;
      allowed_hosts?: string[];
      seed_urls?: string[];
    },
  ) {
    return request<UiAiExplorationStatus>(
      `/api/ui-recordings/${sessionId}/exploration/start`,
      { method: "POST", body: payload },
    );
  },
  getExploration(sessionId: number) {
    return request<UiAiExplorationStatus>(
      `/api/ui-recordings/${sessionId}/exploration`,
    );
  },
  stopExploration(
    sessionId: number,
    payload: { client_instance_id: string; command_id: string },
  ) {
    return request<UiAiExplorationStatus>(
      `/api/ui-recordings/${sessionId}/exploration/stop`,
      { method: "POST", body: payload },
    );
  },
  performMobileAction(
    sessionId: number,
    payload: {
      client_instance_id: string;
      command_id: string;
      action: "tap" | "input" | "swipe" | "back" | "refresh";
      x?: number;
      y?: number;
      end_x?: number;
      end_y?: number;
      duration_ms?: number;
      text?: string;
    },
  ) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/mobile-actions`, {
      method: "POST",
      body: payload,
    });
  },
  performWebAction(
    sessionId: number,
    payload: {
      client_instance_id: string;
      command_id: string;
      action: "click" | "pick" | "input" | "scroll" | "back" | "refresh";
      x?: number;
      y?: number;
      text?: string;
      delta_x?: number;
      delta_y?: number;
    },
  ) {
    return request<UiRecordingSession>(`/api/ui-recordings/${sessionId}/web-actions`, {
      method: "POST",
      body: payload,
    });
  },
  startReplay(
    sessionId: number,
    browser = "chromium",
    options?: {
      headless?: boolean;
      entry_url?: string;
      page_fingerprint?: string;
      page_source_session_id?: number;
      viewport?: { width: number; height: number };
    },
  ) {
    return request<UiOfflineReplay>(`/api/ui-recordings/${sessionId}/replay`, {
      method: "POST",
      body: {
        browser,
        headless: options?.headless ?? false,
        entry_url: options?.entry_url,
        page_fingerprint: options?.page_fingerprint,
        page_source_session_id: options?.page_source_session_id,
        viewport: options?.viewport,
      },
    });
  },
  getReplay(sessionId: number, replayId: string) {
    return request<UiOfflineReplay>(`/api/ui-recordings/${sessionId}/replays/${replayId}`);
  },
  performReplayAction(
    sessionId: number,
    replayId: string,
    payload: {
      action: "click" | "pick" | "input" | "scroll" | "back" | "refresh";
      x?: number;
      y?: number;
      text?: string;
      delta_x?: number;
      delta_y?: number;
      snapshot_id?: number;
    },
  ) {
    return request<UiOfflineReplay>(
      `/api/ui-recordings/${sessionId}/replays/${replayId}/actions`,
      { method: "POST", body: payload },
    );
  },
  stopReplay(sessionId: number, replayId: string) {
    return request<{ status: string }>(
      `/api/ui-recordings/${sessionId}/replays/${replayId}/stop`,
      { method: "POST" },
    );
  },
  async replayImage(sessionId: number, replayId: string): Promise<Blob> {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(
      `/api/ui-recordings/${sessionId}/replays/${replayId}/screenshot`,
      { headers },
    );
    if (!response.ok) {
      throw new ApiError("离线页面画面加载失败", response.status);
    }
    return response.blob();
  },
  stepDraft(sessionId: number) {
    return request<UiRecordingStepDraft>(`/api/ui-recordings/${sessionId}/step-draft`);
  },
  listEvents(
    sessionId: number,
    afterSequence = 0,
    filters?: {
      toSequence?: number;
      source?: string;
      eventType?: string;
      severity?: string;
      keyword?: string;
      limit?: number;
    },
  ) {
    const qs = new URLSearchParams({
      after_sequence: String(afterSequence),
      limit: String(filters?.limit ?? 500),
    });
    if (filters?.toSequence != null) qs.set("to_sequence", String(filters.toSequence));
    if (filters?.source) qs.set("source", filters.source);
    if (filters?.eventType) qs.set("event_type", filters.eventType);
    if (filters?.severity) qs.set("severity", filters.severity);
    if (filters?.keyword?.trim()) qs.set("keyword", filters.keyword.trim());
    return request<UiRecordingEvent[]>(
      `/api/ui-recordings/${sessionId}/events?${qs.toString()}`,
    );
  },
  context(sessionId: number) {
    return request<UiRecordingContextBundle>(`/api/ui-recordings/${sessionId}/context`);
  },
  async executionContext(contextSessionId: number) {
    const pageSize = 1000;
    const first = await request<UiExecutionContextBundle>(
      `/api/ui-context-sessions/${contextSessionId}?limit=${pageSize}`,
    );
    const events = [...first.events];
    const expectedLast = Number(first.context.summary?.last_sequence ?? 0);
    let afterSequence = events.at(-1)?.sequence_no ?? 0;
    while (events.length > 0 && afterSequence < expectedLast) {
      const next = await request<UiExecutionContextBundle>(
        `/api/ui-context-sessions/${contextSessionId}?after_sequence=${afterSequence}&limit=${pageSize}`,
      );
      if (!next.events.length) break;
      events.push(...next.events);
      const nextSequence = next.events.at(-1)?.sequence_no ?? afterSequence;
      if (nextSequence <= afterSequence) break;
      afterSequence = nextSequence;
    }
    return { ...first, events };
  },
  deleteRecording(sessionId: number) {
    return request<{ deleted_id: number; cascade_scope: Record<string, unknown> }>(
      `/api/ui-recordings/${sessionId}?confirm=true`,
      { method: "DELETE" },
    );
  },
  async contextArtifact(artifactId: number): Promise<Blob> {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`/api/ui-context-artifacts/${artifactId}/content`, { headers });
    if (!response.ok) throw new ApiError("上下文制品加载失败", response.status);
    return response.blob();
  },
  listActions(sessionId: number) {
    return request<UiRecordedAction[]>(`/api/ui-recordings/${sessionId}/actions`);
  },
  updateAction(
    sessionId: number,
    actionId: number,
    payload: {
      name?: string;
      status?: UiRecordedAction["status"];
      sequence_no?: number;
      payload?: Record<string, unknown>;
    },
  ) {
    return request<UiRecordedAction>(`/api/ui-recordings/${sessionId}/actions/${actionId}`, {
      method: "PATCH",
      body: payload,
    });
  },
  async listElements(args: {
    projectId: number;
    platform?: UiPlatform;
    pageKey?: string;
    status?: UiElement["status"];
    keyword?: string;
  }) {
    const pageSize = 1000;
    const elements = new Map<number, UiElement>();
    for (let offset = 0; ; offset += pageSize) {
      const qs = new URLSearchParams({
        project_id: String(args.projectId),
        limit: String(pageSize),
        offset: String(offset),
      });
      if (args.platform) qs.set("platform", args.platform);
      if (args.pageKey) qs.set("page_key", args.pageKey);
      if (args.status) qs.set("status", args.status);
      if (args.keyword?.trim()) qs.set("keyword", args.keyword.trim());
      const batch = await request<UiElement[]>(`/api/ui-elements?${qs.toString()}`);
      const previousSize = elements.size;
      for (const element of batch) elements.set(element.id, element);
      if (batch.length < pageSize || elements.size === previousSize) break;
    }
    return [...elements.values()];
  },
  updateElement(
    elementId: number,
    payload: {
      semantic_name?: string;
      aliases?: string[];
      status?: UiElement["status"];
    },
  ) {
    return request<UiElement>(`/api/ui-elements/${elementId}`, {
      method: "PATCH",
      body: payload,
    });
  },
  deleteElement(elementId: number) {
    return request<{ deleted_id: number; cascade_scope: Record<string, unknown> }>(
      `/api/ui-elements/${elementId}?confirm=true`,
      { method: "DELETE" },
    );
  },
  createLocator(
    elementId: number,
    payload: { strategy: string; locator: string; score?: number; is_primary?: boolean },
  ) {
    return request<UiElement>(`/api/ui-elements/${elementId}/locators`, {
      method: "POST",
      body: payload,
    });
  },
  updateLocator(
    elementId: number,
    locatorId: number,
    payload: { strategy?: string; locator?: string; score?: number; is_primary?: boolean },
  ) {
    return request<UiElement>(`/api/ui-elements/${elementId}/locators/${locatorId}`, {
      method: "PATCH",
      body: payload,
    });
  },
  deleteLocator(elementId: number, locatorId: number) {
    return request<UiElement>(`/api/ui-elements/${elementId}/locators/${locatorId}`, {
      method: "DELETE",
    });
  },
  validateLocator(elementId: number, locatorId: number, snapshotId?: number) {
    const query = snapshotId ? `?snapshot_id=${snapshotId}` : "";
    return request<UiElement>(
      `/api/ui-elements/${elementId}/locators/${locatorId}/validate${query}`,
      { method: "POST" },
    );
  },
  listSnapshots(args: { projectId: number; platform?: UiPlatform; pageKey?: string }) {
    const qs = new URLSearchParams({ project_id: String(args.projectId) });
    if (args.platform) qs.set("platform", args.platform);
    if (args.pageKey) qs.set("page_key", args.pageKey);
    return request<UiPageSnapshot[]>(`/api/ui-page-snapshots?${qs.toString()}`);
  },
  updateSnapshot(
    snapshotId: number,
    payload: { page_name?: string; state_name?: string; apply_page_name_to_group?: boolean },
  ) {
    return request<UiPageSnapshot>(`/api/ui-page-snapshots/${snapshotId}`, {
      method: "PATCH",
      body: payload,
    });
  },
  pickSnapshot(snapshotId: number, payload: { x: number; y: number }) {
    return request<UiElement>(`/api/ui-page-snapshots/${snapshotId}/pick`, {
      method: "POST",
      body: payload,
    });
  },
  prepareSnapshot(snapshotId: number) {
    return request<{ ready: boolean; reused: boolean }>(
      `/api/ui-page-snapshots/${snapshotId}/prepare`,
      { method: "POST" },
    );
  },
  deletePageGroup(args: { projectId: number; platform: UiPlatform; pageKey: string }) {
    const qs = new URLSearchParams({
      project_id: String(args.projectId),
      platform: args.platform,
      page_key: args.pageKey,
      confirm: "true",
    });
    return request<{ page_key: string; cascade_scope: Record<string, unknown> }>(
      `/api/ui-page-groups?${qs.toString()}`,
      { method: "DELETE" },
    );
  },
  async snapshotImage(snapshotId: number): Promise<Blob> {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(`/api/ui-page-snapshots/${snapshotId}/screenshot`, { headers });
    if (!response.ok) {
      throw new ApiError("页面截图加载失败", response.status);
    }
    return response.blob();
  },
};

// ---------------------------------------------------------------------------
// 知识库（项目管理 → 知识库 tab）
// ---------------------------------------------------------------------------
export const knowledgeApi = {
  list(projectId: number, opts: { module_id?: number | null } = {}) {
    const qs = new URLSearchParams({ project_id: String(projectId) });
    if (opts.module_id != null) qs.set("module_id", String(opts.module_id));
    return request<KnowledgeDoc[]>(`/api/knowledge?${qs.toString()}`);
  },
  get(id: number) {
    return request<KnowledgeDoc>(`/api/knowledge/${id}`);
  },
  create(body: KnowledgeDocCreate) {
    return request<KnowledgeDoc>("/api/knowledge", { method: "POST", body });
  },
  update(id: number, body: KnowledgeDocUpdate) {
    return request<KnowledgeDoc>(`/api/knowledge/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<{ id: number }>(`/api/knowledge/${id}`, { method: "DELETE" });
  },
};
