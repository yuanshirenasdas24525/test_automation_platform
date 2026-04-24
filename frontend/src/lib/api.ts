/**
 * 薄薄一层 fetch 封装。所有后端接口都返回 `{status, data?, message?}` 信封，
 * 这里统一帮你剥壳：业务失败（status==="error"）和网络错误都抛成 `ApiError`，
 * 这样上游用 try/catch 或 TanStack Query 都省事。
 */
import type {
  ApiEnvelope,
  ContentNode,
  Module,
  ModuleCreate,
  Project,
  ProjectCategory,
  ProjectCreate,
  ReorderItem,
  RunTestRequest,
  RunTestResult,
  TestCaseCreate,
  TestCaseDetail,
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
      (payload as ApiEnvelope)?.message ?? `HTTP ${res.status} ${res.statusText}`;
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
  list(type?: ProjectCategory | string) {
    const qs = type ? `?type=${encodeURIComponent(type)}` : "";
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
};

// -------------------------------------------------------------------------
// Content tree（模块 + 用例混合结构）
// -------------------------------------------------------------------------
export const contentApi = {
  /** 列出一个 project 下 parent_id=X 的子节点（模块 + 用例）。 */
  list(projectId: number, parentId?: number | null) {
    const qs = parentId != null ? `?parent_id=${parentId}` : "";
    return request<ContentNode[]>(`/api/content/${projectId}${qs}`);
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
  list(category?: string) {
    const qs = category ? `?category=${encodeURIComponent(category)}` : "";
    return request<ConfigItem[]>(`/api/config/all${qs}`);
  },
  schema(category: string) {
    return request<ConfigSchemaItem[]>(
      `/api/config/schema/${encodeURIComponent(category)}`,
    );
  },
  save(body: Omit<ConfigItem, "id">) {
    return request<void>("/api/config/save", { method: "POST", body });
  },
  add(body: Omit<ConfigItem, "id">) {
    return request<void>("/api/config/add", { method: "POST", body });
  },
  remove(id: number) {
    return request<void>(`/api/config/delete/${id}`, { method: "DELETE" });
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
