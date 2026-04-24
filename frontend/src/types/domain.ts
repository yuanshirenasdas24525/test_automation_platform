/**
 * 领域类型：和后端 `src/database/models` + `src/database/schemas` 对齐。
 *
 * 这里先只写前端真正用到的字段，后续补全时只改这一个文件即可。
 */

/** 后端统一响应信封：`{status, data?, message?}` */
export interface ApiEnvelope<T = unknown> {
  status: "success" | "error";
  data?: T;
  message?: string;
}

/** 项目类型 —— 对应后端 projects.type 字段。后期可能加 "load" / "ai" 等。 */
export type ProjectCategory = "api" | "web" | "app";

/** 用例类型 —— 对应 test_cases.case_type。mixed 是 web+app / api+web 这种混合。 */
export type CaseType = "api" | "web" | "app" | "mixed";

/**
 * 一条 step 的"前端草稿"形态：在 CaseDialog 里增删改时用的结构，和后端
 * schemas/test_step_schema.py 的 TestStepCreate 对齐。`config` 字段由 step_type
 * 决定；前端用 record<string, any> 存，提交前交给后端校验。
 */
export interface TestStepDraft {
  /** 编辑态时后端给的 id；新建行为 null */
  id?: number | null;
  step_order: number;
  step_name: string;
  step_type: string;
  skip?: boolean;
  config: Record<string, unknown>;
  extract?: Record<string, unknown>[] | null;
  assertion?: Record<string, unknown>[] | null;
  wait_before?: number;
  timeout?: number;
  retry?: number;
  on_failure?: "stop" | "continue" | "retry";
}

export interface Project {
  id: number;
  name: string;
  type: ProjectCategory | string;
  desc?: string | null;
  description?: string | null;
  /** 列表接口带上的聚合字段，详情里可能没有 */
  case_count?: number;
  pass_rate?: number;
  last_status?: string | null;
  last_run_time?: string | null;
}

export interface ProjectCreate {
  name: string;
  type: ProjectCategory | string;
  description?: string;
}

/** 目录树上的一个节点：可能是"模块"也可能是"用例"。 */
export interface ContentNode {
  id: number;
  type: "module" | "case";
  name: string;
  parent_id?: number | null;
  module_id?: number | null;
  sort_order?: number;
  /** 用例才有这些字段 */
  description?: string | null;
  skip?: boolean;
  case_type?: CaseType | null;
  method?: string | null;
  path?: string | null;
  headers?: string | null;
  data_type?: string | null;
  params?: string | null;
  file_path?: string | null;
  extract_data?: string | null;
  sql_query?: string | null;
  assertion?: string | null;
  wait_time?: number | null;
  last_status?: string | null;
}

/** 用例详情 —— 创建 / 编辑表单对应。 */
export interface TestCaseCreate {
  module_id: number;
  name: string;
  description?: string | null;
  skip?: boolean;
  sort_order?: number | null;

  /** v2 新增字段 */
  case_type?: CaseType | null;
  tags?: string[] | null;
  priority?: number | null;
  /**
   * Web / App 用例真正的"步骤列表"。传 null/undefined 表示本次不改动后端的 steps；
   * 传 [] 表示显式清空；传数组则整体替换。
   */
  steps?: TestStepDraft[] | null;

  /** v1 遗留 HTTP 字段（API 用例继续用） */
  method?: string | null;
  path?: string | null;
  headers?: string | null;
  data_type?: string | null;
  params?: string | null;
  file_path?: string | null;
  extract_data?: string | null;
  sql_query?: string | null;
  assertion?: string | null;
  wait_time?: number | null;
}

/** GET /api/test_cases/:id 的返回体 data 字段形状。 */
export interface TestCaseDetail extends TestCaseCreate {
  id: number;
  steps: TestStepDraft[];
}

export interface Module {
  id: number;
  name: string;
  parent_id: number | null;
  project_id: number;
  /** 后端面包屑用：根 → 父，顺序从外到里。 */
  ancestors?: { id: number | null; name: string }[];
}

/** 拖拽 / 插入时用的 reorder payload。 */
export interface ReorderItem {
  id: number;
  type: "module" | "case";
  new_order: number;
}

export interface ModuleCreate {
  project_id: number;
  name: string;
  parent_id?: number | null;
}

export interface RunTestRequest {
  project: number;
  module?: number | null;
  case?: number | null;
  category: ProjectCategory | string;
  v2?: boolean;
  /** 只在 app 场景下生效：指定某台已 idle 的设备运行，忽略 env 的 device_pool 过滤。 */
  device_id?: number | null;
}

export interface RunTestResult {
  status: "success" | "error";
  report_id?: number;
  task_id?: string;
  case_number?: number;
  message?: string;
}
