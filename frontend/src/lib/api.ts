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
  AiModelConfig,
  AiModelConfigUpsert,
  AiModelTestResult,
  AiRun,
  AiRunStatus,
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
  CaseType,
  ChangePasswordRequest,
  ContentNode,
  FunctionalBatchMarkPayload,
  FunctionalBatchSummary,
  FunctionalCase,
  FunctionalCaseCreate,
  FunctionalCaseRun,
  FunctionalCaseUpdate,
  FunctionalMarkPayload,
  FunctionalRunStatus,
  LoginRequest,
  LoginResponse,
  Module,
  ModuleCreate,
  Project,
  ProjectCreate,
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
  RequirementSplitItem,
  RequirementUpdate,
  Role,
  RunTestRequest,
  RunTestResult,
  Task,
  TaskCreate,
  TaskFromTestFailurePayload,
  TaskListFilters,
  TaskUpdate,
  TestCaseCreate,
  TestCaseDetail,
  User,
  UserCreate,
  UserUpdate,
  VersionBoard,
  VersionCase,
  VersionCreate,
  VersionPickerItem,
  VersionTestSummary,
  VersionUpdate,
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

// ---------------------------------------------------------------------------
// Token 管理
// ---------------------------------------------------------------------------
const TOKEN_KEY = "pm.authToken";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

type RequestInitJSON = Omit<RequestInit, "body"> & {
  body?: unknown;
};

async function request<T>(path: string, init: RequestInitJSON = {}): Promise<T> {
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
    const msg =
      (payload as Record<string, unknown>)?.detail as string ||
      (payload as ApiEnvelope)?.message ||
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
  create(body: TestCaseCreate) {
    return request<{ id: number }>("/api/test_cases", {
      method: "POST",
      body,
    });
  },
  update(id: number, body: TestCaseCreate) {
    return request<void>(`/api/test_cases/${id}`, { method: "PUT", body });
  },
  /** 拉一个用例的详情（含 steps），给 Web/App 编辑态用。 */
  get(id: number) {
    return request<TestCaseDetail>(`/api/test_cases/${id}`);
  },
  remove(id: number) {
    return request<void>(`/api/test_cases/${id}`, { method: "DELETE" });
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
    const resp = await fetch(url, { method: "GET" });
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
    document.body.removeChild(a);
    URL.revokeObjectURL(objectUrl);
  },
};

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
  /** 创建一条功能用例（写入 test_cases，case_type='functional'）。 */
  create(body: FunctionalCaseCreate) {
    return request<FunctionalCase>("/api/functional_cases", {
      method: "POST",
      body,
    });
  },
  /** 部分更新；后端用 exclude_unset 区分"没传 vs 传了 null"。 */
  update(id: number, body: FunctionalCaseUpdate) {
    return request<FunctionalCase>(`/api/functional_cases/${id}`, {
      method: "PUT",
      body,
    });
  },
  /** 删除（关联 FunctionalCaseRun 会随 cascade 一起删）。 */
  remove(id: number) {
    return request<void>(`/api/functional_cases/${id}`, { method: "DELETE" });
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
    const res = await fetch(url);
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
  project_id: number | null;
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

export const configApi = {
  list(category?: string, projectId?: number | null) {
    const qs = new URLSearchParams();
    if (category) qs.set("category", category);
    if (projectId != null) qs.set("project_id", String(projectId));
    const q = qs.toString();
    return request<ConfigItem[]>(`/api/config/all${q ? `?${q}` : ""}`);
  },
  schema(category: string) {
    return request<ConfigSchemaItem[]>(
      `/api/config/schema/${encodeURIComponent(category)}`,
    );
  },
  save(body: Omit<ConfigItem, "id" | "project_id"> & { project_id?: number | null }) {
    return request<void>("/api/config/save", { method: "POST", body });
  },
  add(body: Omit<ConfigItem, "id" | "project_id"> & { project_id?: number | null }) {
    return request<void>("/api/config/add", { method: "POST", body });
  },
  remove(id: number) {
    return request<void>(`/api/config/delete/${id}`, { method: "DELETE" });
  },
  copyFromGlobal(projectId: number, categories?: string[]) {
    return request<{ copied: number }>("/api/config/copy-from-global", {
      method: "POST",
      body: { project_id: projectId, categories },
    });
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
  create_time: string | null;
}

export interface TestReportDetail extends TestReportSummary {
  steps: TestStepReportItem[];
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
  const res = await fetch(path, { headers: { Accept: "application/json" } });
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
    return request<{ deleted_ids: number[] }>(`/api/requirements/${id}`, {
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
      body: payload,
    });
  },
  me() {
    return request<User>("/api/auth/me");
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
  list() {
    return request<AiModelConfig[]>("/api/ai-models");
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
    body: { model_names: string[]; user_prompt?: string; document_title?: string },
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
  /** 逻辑删：status → rejected。 */
  rejectDraft(id: number) {
    return request<AiCaseDraft>(`/api/ai/case-drafts/${id}`, {
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
};
