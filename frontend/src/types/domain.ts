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

/**
 * 项目支持的"栈"。
 *
 * 一个项目可以同时启用多个栈（Functional + API + Web + Android + iOS），
 * `Project.enabled_stacks` 是这个集合的子集。前端在项目详情页用 Tab 切栈视图。
 *
 * 历史上有一个 "app" 兼容栈，已通过 app_to_android 迁移转成 android/ios。
 */
export type ProjectStack =
  | "api"
  | "web"
  | "android"
  | "ios"
  | "functional";

/** 全部栈 + 固定展示顺序（功能 → API → Web → Android → iOS）。
 * 卡片 chips / Tab 顺序都按这个数组渲染，不依赖入库顺序。 */
export const ALL_PROJECT_STACKS: ProjectStack[] = [
  "functional",
  "api",
  "web",
  "android",
  "ios",
];

/** 新建项目表单展示的栈选项 —— 与 ALL_PROJECT_STACKS 等价；保留单独导出名是
 *  为了后续如果想"显示但不让新建"再细分。 */
export const NEW_PROJECT_STACKS: ProjectStack[] = ALL_PROJECT_STACKS;

/**
 * 用例类型 —— 对应 test_cases.case_type。
 * - api / web / android / ios：单栈用例
 * - mixed：跨栈用例（如 API 拿 token → Web 登录），后端按"第一步骤所属栈"归到对应 Tab
 * - functional：人工功能用例，没 steps，结果靠测试人员"勾"
 */
export type CaseType =
  | "api"
  | "web"
  | "android"
  | "ios"
  | "mixed"
  | "functional";

/** 走自动化执行链路的 case_type 集合（functional 不在内）。 */
export const AUTOMATED_CASE_TYPES: CaseType[] = [
  "api",
  "web",
  "android",
  "ios",
  "mixed",
];

/** 需要 Appium 设备的 case_type 集合（前端判断"要不要弹设备选择器"）。 */
export const APP_CASE_TYPES: CaseType[] = ["android", "ios"];

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
  /**
   * 启用的栈集合。后端保证非空、值 ⊆ ALL_PROJECT_STACKS。
   * 前端展示 chip 时按 ALL_PROJECT_STACKS 顺序过滤渲染，不要直接用入库顺序。
   */
  enabled_stacks: ProjectStack[];
  desc?: string | null;
  description?: string | null;
  icon?: string | null;
  /** 列表接口带上的聚合字段，详情里可能没有 */
  case_count?: number;
  pass_rate?: number;
  last_status?: string | null;
  last_run_time?: string | null;
}

export interface ProjectCreate {
  name: string;
  /** 至少一个；前端 form 校验 + 后端 pydantic 双重保证 */
  enabled_stacks: ProjectStack[];
  description?: string;
  icon?: string;
}

/** GET /api/projects/{id}/stack_counts —— 项目详情页 Tab 角标用。 */
export interface ProjectStackCounts {
  project_id: number;
  enabled_stacks: ProjectStack[];
  /** 每种 case_type 下的用例数；含未启用栈（可能 = 0）和 mixed */
  counts: Record<CaseType, number>;
  total: number;
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
  /** "category" 命名是历史遗留，语义对应 ProjectStack（functional 后端会拒绝）。 */
  category: Exclude<ProjectStack, "functional"> | string;
  /** 只在 app/android/ios 场景下生效：指定某台已 idle 的设备运行，忽略 env 的 device_pool 过滤。 */
  device_id?: number | null;
}

export interface RunTestResult {
  status: "success" | "error";
  report_id?: number;
  task_id?: string;
  case_number?: number;
  message?: string;
}

// =============================================================================
// 功能用例（人工执行）—— 对应后端 /api/functional_cases/* 路由
// =============================================================================

/** functional 用例的"步骤 / 期望"自由文本结构，对应 TestCase.functional_spec JSON 列。 */
export interface FunctionalSpec {
  preconditions: string[];
  steps: string[];
  expected?: string | null;
}

/** 一次"勾结果"的状态枚举。pending 是前端合成（未执行过），不入库。 */
export type FunctionalRunStatus = "passed" | "failed" | "blocked" | "na" | "pending";

export interface FunctionalCaseRun {
  id: number;
  case_id: number;
  status: Exclude<FunctionalRunStatus, "pending">;
  actual_result?: string | null;
  note?: string | null;
  operator?: string | null;
  /** ISO 字符串 */
  executed_at: string;
  batch_id?: string | null;
}

export interface FunctionalCase {
  id: number;
  module_id: number;
  name: string;
  description?: string | null;
  skip: boolean;
  priority?: number | null;
  tags: string[];
  case_type: "functional";
  sort_order?: number | null;
  functional_spec: FunctionalSpec;
  /** 最近一次"勾"，没有就是 null（前端展示 pending） */
  latest_run: FunctionalCaseRun | null;
}

export interface FunctionalCaseCreate {
  module_id: number;
  name: string;
  description?: string | null;
  skip?: boolean;
  priority?: number | null;
  tags?: string[] | null;
  functional_spec?: FunctionalSpec | null;
  sort_order?: number | null;
}

export interface FunctionalCaseUpdate {
  module_id?: number;
  name?: string;
  description?: string | null;
  skip?: boolean;
  priority?: number | null;
  tags?: string[] | null;
  functional_spec?: FunctionalSpec | null;
}

export interface FunctionalMarkPayload {
  status: Exclude<FunctionalRunStatus, "pending">;
  actual_result?: string | null;
  note?: string | null;
  operator?: string | null;
  /** "测试模式"批量勾的批次 id；单点勾可不传 */
  batch_id?: string | null;
}

export interface FunctionalBatchItem {
  case_id: number;
  status: Exclude<FunctionalRunStatus, "pending">;
  actual_result?: string | null;
  note?: string | null;
}

export interface FunctionalBatchMarkPayload {
  batch_id: string;
  operator?: string | null;
  items: FunctionalBatchItem[];
}

/** /api/functional_cases/batches 返回的批次概览。 */
export interface FunctionalBatchSummary {
  batch_id: string;
  started_at: string | null;
  finished_at: string | null;
  total: number;
  passed: number;
  failed: number;
  blocked: number;
  na: number;
  pass_rate: number;
}
