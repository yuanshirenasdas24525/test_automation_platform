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

export type ApiRunStatus = "passed" | "failed" | "error" | "skipped" | "pending";

export interface ApiCaseLatestRun {
  status: ApiRunStatus;
  report_id: number;
  executed_at: string | null;
  duration: number;
}

export interface ApiCaseLatestRunStepDetail {
  step_report_id: number;
  step_id: number | null;
  step_name: string | null;
  step_type: string | null;
  status: string | null;
  status_code: number | null;
  duration: number | null;
  request: {
    method: string | null;
    url: string | null;
    headers: unknown;
    params: unknown;
  };
  response: unknown;
  assertion: {
    configured: unknown;
    results: unknown;
  };
  extract: {
    configured: unknown;
    values: unknown;
  };
  error_message: string | null;
  create_time: string | null;
}

export interface ApiCaseLatestRunDetail {
  case_id: number;
  case_name: string;
  report_id: number;
  status: ApiRunStatus;
  executed_at: string | null;
  duration: number;
  variable_pool: Record<string, unknown>;
  steps: ApiCaseLatestRunStepDetail[];
}

export interface ApiCase extends TestCaseCreate {
  id: number;
  module_id: number;
  case_type: "api";
  tags: string[];
  skip: boolean;
  latest_run: ApiCaseLatestRun | null;
}

export interface ApiCaseListResponse {
  items: ApiCase[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiCaseRunResult {
  case_id: number;
  case_name: string;
  status: ApiRunStatus;
  duration: number;
  error_message: string | null;
}

export interface ApiTestHistoryReport {
  report_id: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration: number | null;
  executor: string | null;
  allure_url: string | null;
  counts: Record<ApiRunStatus, number>;
  cases: ApiCaseRunResult[];
}

export type ApiCaseEditRecord = FunctionalCaseEditRecord;

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
  /** 批量运行指定用例，后端合并为同一份执行报告。 */
  case_ids?: number[] | null;
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

/** 模块测试历史里的一条勾结果，带用例名（来自 /test_history）。 */
export interface FunctionalTestHistoryRun extends FunctionalCaseRun {
  case_name: string;
}

/** AI 生成的一条功能用例草稿（来自 /functional_cases/ai_generate_batch）。 */
export interface AiGeneratedCase {
  name: string;
  preconditions: string[];
  steps: string[];
  expected: string[];
  /** 与模块现有用例重名（后端标记），前端默认不勾、显示「已存在」。 */
  duplicate?: boolean;
  /** AI 建议插入位置：排在哪条现有用例之后（其名称）；"__START__" 最前；"" 末尾。 */
  after?: string;
  /** 接口模式的结构化字段（映射到 api 用例的 method/path/headers/body/提取/断言/SQL）。 */
  method?: string;
  path?: string;
  headers?: Record<string, unknown>;
  body?: Record<string, unknown>;
  extract?: Record<string, unknown>;
  assertion?: Record<string, unknown>;
  sql?: string;
}

/** AI 规划出的一个测试点（来自 /functional_cases/ai_generate_outline）。 */
export interface AiOutlinePoint {
  title: string;
  category: string;
}

/** AI 项目概览（模块关联图谱，存在项目上）。 */
export interface ProjectAiOverview {
  summary: string;
  modules: { name: string; purpose: string }[];
  relations: { from: string; to: string; relation: string }[];
}

/** /projects/{id}/ai_overview 的返回。 */
export interface ProjectAiOverviewResp {
  overview: ProjectAiOverview | null;
  updated_at: string | null;
}

export type FunctionalEditAction = "create" | "update" | "delete";

export interface FunctionalEditChange {
  field: string;
  old: string;
  new: string;
}

/** 功能用例编辑历史（新建/修改/删除）一条记录（来自 /edit_history）。 */
export interface FunctionalCaseEditRecord {
  id: number;
  batch_id?: number;
  case_id: number | null;
  module_id: number | null;
  case_name: string | null;
  action: FunctionalEditAction;
  changes: FunctionalEditChange[];
  /** 快速编辑会话 id：同会话的多条改动聚合成一条编辑记录 */
  session_id: string | null;
  operator: string | null;
  /** ISO 字符串 */
  created_at: string;
  rollback_available?: boolean;
  rollback_status?: "none" | "partial" | "full" | "rolled_back";
  snapshot_expires_at?: string | null;
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

// =============================================================================
// AI Phase A —— ai_runs / requirements
// =============================================================================

/** AI 任务状态（跟后端 AI_RUN_STATUS_* 对齐）。 */
export type AiRunStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "cancelled";

/** AI 功能名（跟后端 AI_FEATURE_* 对齐）。 */
export type AiFeature =
  | "requirement_parse"
  | "test_plan"
  | "functional_case_gen"
  | "functional_case_review"
  | "api_case_gen"
  | "report_summary"
  | "test_result_analysis"
  | "functional_to_auto"
  | "load_plan_gen";

/** AI 任务记录。前端轮询这个 endpoint 拿状态 / 拿结果。 */
export interface AiRun {
  id: number;
  feature: AiFeature | string;
  status: AiRunStatus;
  project_id?: number | null;
  celery_task_id?: string | null;
  input_payload?: Record<string, unknown> | null;
  output_payload?: Record<string, unknown> | null;
  error?: string | null;
  provider?: string | null;
  model?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  cost_usd?: number | null;
  prompt_hash?: string | null;
  prompt_version?: string | null;
  operator?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
}

/** 需求点（来自 requirements 表）。 */
export type RequirementStatus = "draft" | "approved" | "archived";
export type RequirementSource = "manual" | "ai_generated";

/** PM 重设计：自动算的开发态。后端 task_service.recompute_requirement_status() 维护。
 *  M5：pm_review 改为 task-driven；done 仍由 PM 推进。 */
export const ALL_REQUIREMENT_SYSTEM_STATUSES = [
  "approved",
  "developing",
  "testing",
  "ready_to_release",
  "pm_review",
  "done",
] as const;
export type RequirementSystemStatus =
  (typeof ALL_REQUIREMENT_SYSTEM_STATUSES)[number];

/** M5：四角色 assignee（dev/test/pm/ui），值是 user_id 数组。 */
export interface RequirementAssignees {
  dev: number[];
  test: number[];
  pm: number[];
  ui: number[];
}

/** PM 维护的业务态。 */
export const ALL_REQUIREMENT_BUSINESS_STATUSES = [
  "approved",
  "accepted",
  "released",
] as const;
export type RequirementBusinessStatus =
  (typeof ALL_REQUIREMENT_BUSINESS_STATUSES)[number];

export interface Requirement {
  id: number;
  project_id: number;
  title: string;
  description?: string | null;
  acceptance_criteria: string[];
  priority: number;
  tags: string[];
  depends_on: number[];
  status: RequirementStatus;
  source: RequirementSource;
  ai_run_id?: number | null;
  sort_order: number;
  /** PM 重设计：版本归属 + 双状态轨 + PM 指派 + 验收时间 */
  version_id?: number | null;
  system_status?: RequirementSystemStatus | null;
  business_status?: RequirementBusinessStatus | null;
  assignee_pm_id?: number | null;
  accepted_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** M5：TAPD 化字段 */
  parent_requirement_id?: number | null;
  module_id?: number | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  assignees?: RequirementAssignees;
  /** tree 模式下顶层节点会返回 children；扁平模式不返回 */
  children?: Requirement[];
}

export interface RequirementCreate {
  project_id: number;
  title: string;
  description?: string | null;
  acceptance_criteria?: string[] | null;
  priority?: number;
  tags?: string[] | null;
  depends_on?: number[] | null;
  status?: RequirementStatus;
  version_id?: number | null;
  business_status?: RequirementBusinessStatus | null;
  assignee_pm_id?: number | null;
  parent_requirement_id?: number | null;
  module_id?: number | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  assignees?: Partial<RequirementAssignees>;
}

export interface RequirementUpdate {
  title?: string;
  description?: string | null;
  acceptance_criteria?: string[] | null;
  priority?: number;
  tags?: string[] | null;
  depends_on?: number[] | null;
  status?: RequirementStatus;
  version_id?: number | null;
  business_status?: RequirementBusinessStatus | null;
  assignee_pm_id?: number | null;
  parent_requirement_id?: number | null;
  module_id?: number | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  assignees?: Partial<RequirementAssignees>;
}

export interface RequirementSplitItem {
  title: string;
  description?: string | null;
  priority?: number;
  module_id?: number | null;
  version_id?: number | null;
  planned_start_at?: string | null;
  planned_end_at?: string | null;
}

/** 附件（M5）。 */
export type AttachmentKind = "link" | "file";
export interface Attachment {
  id: number;
  requirement_id: number;
  name: string;
  kind: AttachmentKind;
  url: string;
  size_bytes?: number | null;
  uploaded_by_id?: number | null;
  created_at?: string | null;
}

/** 需求编辑历史（M6）。 */
export interface RequirementEditHistoryChange {
  field: string;
  label: string;
  old: unknown;
  new: unknown;
}
export interface RequirementEditHistory {
  id: number;
  batch_id?: number;
  requirement_id: number;
  edited_by_id?: number | null;
  action?: "create" | "update" | "delete" | "mixed";
  entity_label?: string | null;
  changes: RequirementEditHistoryChange[];
  change_summary?: string | null;
  created_at?: string | null;
  rollback_available?: boolean;
  rollback_status?: "none" | "partial" | "full" | "rolled_back";
  snapshot_expires_at?: string | null;
}

export interface RequirementRollbackPayload {
  mode: "full" | "partial" | "fields";
  event_ids?: number[];
  fields?: Record<string, string[]>;
  operator_id?: number | null;
  reason?: string | null;
  force?: boolean;
}

export interface RequirementRollbackResult {
  rolled_back: number;
  rollback_batch_id?: number;
  rollback_event_ids?: number[];
  conflicts: Array<{
    event_id: number;
    entity_id?: number | null;
    field: string;
    before: unknown;
    after: unknown;
    current: unknown;
    message: string;
  }>;
}

/** PM 验收 payload（pm_id 可选；不传时后端不强制校验角色）。 */
export interface RequirementAcceptPayload {
  pm_id?: number | null;
}

// =============================================================================
// 版本迭代（ProjectVersion）
// =============================================================================

export type VersionStatus =
  | "planning"
  | "developing"
  | "testing"
  | "ready_to_release"
  | "released"
  | "archived";

export interface VersionEntry {
  version: string;
  date: string;
  notes?: string;
}

export interface DocItem {
  id: string;
  name: string;
  type: "link" | "text" | "file";
  url?: string | null;
  content?: string | null;
}

export interface ProjectVersion {
  id: number;
  project_id: number;
  version_name: string;
  display_name?: string | null;
  status: VersionStatus;
  sort_order: number;
  frontend_versions: VersionEntry[];
  backend_versions: VersionEntry[];
  release_notes: string;
  test_plan_items: DocItem[];
  requirement_doc_items: DocItem[];
  design_doc_items: DocItem[];
  ui_prototype_items: DocItem[];
  associated_module_ids: number[];
  planned_start_at?: string | null;
  planned_end_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** M5：派生字段，后端按 planned_end_at < now() 且 status 非 released/archived 计算 */
  is_delayed?: boolean;
}

export interface VersionCreate {
  version_name: string;
  display_name?: string;
  status?: VersionStatus;
  module_ids?: number[];
  frontend_versions?: VersionEntry[];
  backend_versions?: VersionEntry[];
  release_notes?: string;
  test_plan_items?: DocItem[];
  requirement_doc_items?: DocItem[];
  design_doc_items?: DocItem[];
  ui_prototype_items?: DocItem[];
  planned_start_at?: string;
  planned_end_at?: string;
}

export interface VersionUpdate {
  version_name?: string;
  display_name?: string;
  status?: VersionStatus;
  sort_order?: number;
  frontend_versions?: VersionEntry[];
  backend_versions?: VersionEntry[];
  release_notes?: string;
  test_plan_items?: DocItem[];
  requirement_doc_items?: DocItem[];
  design_doc_items?: DocItem[];
  ui_prototype_items?: DocItem[];
  planned_start_at?: string;
  planned_end_at?: string;
}

/** GET /api/versions/picker 返回的版本简要信息。 */
export interface VersionPickerItem {
  id: number;
  version_name: string;
  project_id: number;
  project_name: string;
}

// =============================================================================
// PM 重设计 · 用户 / 角色 / 任务 / 版本汇总（M3）
// =============================================================================

/** 后端 6 个固定 role code（database/models/role.py:ALL_ROLE_CODES）。 */
export const ALL_ROLE_CODES = [
  "admin",
  "dev",
  "test",
  "pm",
  "ui",
  "ops",
] as const;
export type RoleCode = (typeof ALL_ROLE_CODES)[number];

/** Role 中文展示名（侧边栏 / 切换器 tab 用）。 */
export const ROLE_LABELS: Record<RoleCode, string> = {
  admin: "管理员",
  dev: "开发",
  test: "测试",
  pm: "产品",
  ui: "设计",
  ops: "运维",
};

export interface Role {
  id: number;
  code: RoleCode | string;
  name?: string | null;
  description?: string | null;
}

export interface User {
  id: number;
  username: string;
  full_name?: string | null;
  email?: string | null;
  is_active: boolean;
  role_codes: string[];
  created_at?: string | null;
  updated_at?: string | null;
}

export interface UserCreate {
  username: string;
  full_name?: string | null;
  email?: string | null;
  password?: string | null;
  is_active?: boolean;
  role_codes?: string[] | null;
}

export interface UserUpdate {
  username?: string | null;
  full_name?: string | null;
  email?: string | null;
  password?: string | null;
  is_active?: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
  user: User;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

/** Task 类型（database/models/task.py:ALL_TASK_TYPES）。
 *  M5：新增 pm_review = 产品体验 / PM 走查 */
export const ALL_TASK_TYPES = [
  "dev",
  "test",
  "ui_review",
  "pm_review",
  "bug",
] as const;
export type TaskType = (typeof ALL_TASK_TYPES)[number];

/** Task 状态机线性枚举。bug 解耦：自己走 dev_doing → closed/passed。 */
export const ALL_TASK_STATUSES = [
  "pending",
  "dev_doing",
  "dev_done",
  "test_doing",
  "passed",
  "failed",
  "closed",
] as const;
export type TaskStatus = (typeof ALL_TASK_STATUSES)[number];

/** Bug 严重等级（仅 type=bug 时填）。 */
export const ALL_BUG_SEVERITIES = ["P0", "P1", "P2", "P3"] as const;
export type BugSeverity = (typeof ALL_BUG_SEVERITIES)[number];

export interface Task {
  id: number;
  requirement_id: number;
  parent_task_id?: number | null;
  version_id?: number | null;
  title: string;
  description?: string | null;
  type: TaskType;
  status: TaskStatus;
  severity?: BugSeverity | null;
  assignee_dev_id?: number | null;
  assignee_test_id?: number | null;
  created_by_id?: number | null;
  related_case_id?: number | null;
  metadata: Record<string, unknown>;
  estimated_hours?: number | null;
  actual_hours?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
  closed_at?: string | null;
  // AI 修复 Bug 相关字段
  fix_description?: string | null;
  fix_commit_sha?: string | null;
  fix_commit_branch?: string | null;
  fix_suggestion?: string | null;
  fix_agent_used?: string | null;
  fix_ai_run_id?: number | null;
}

export interface TaskCreate {
  requirement_id: number;
  title: string;
  type: TaskType;
  created_by_id: number;
  description?: string | null;
  status?: TaskStatus;
  severity?: BugSeverity | null;
  assignee_dev_id?: number | null;
  assignee_test_id?: number | null;
  related_case_id?: number | null;
  metadata?: Record<string, unknown> | null;
  estimated_hours?: number | null;
  parent_task_id?: number | null;
  version_id?: number | null;
}

export interface TaskUpdate {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  severity?: BugSeverity | null;
  assignee_dev_id?: number | null;
  assignee_test_id?: number | null;
  related_case_id?: number | null;
  metadata?: Record<string, unknown> | null;
  estimated_hours?: number | null;
  actual_hours?: number | null;
}

/** Tasks 列表过滤参数（GET /api/tasks）。closed_at_after 是 ISO 字符串。 */
export interface TaskListFilters {
  requirement_id?: number;
  assignee_dev_id?: number;
  assignee_test_id?: number;
  type?: TaskType;
  status?: TaskStatus;
  created_by_id?: number;
  closed_at_after?: string;
  parent_task_id?: number;
  ids?: number[];
  version_id?: number;
}

/** POST /api/tasks/from-test-failure payload。 */
export interface TaskFromTestFailurePayload {
  parent_task_id?: number | null;
  version_id?: number | null;
  severity: BugSeverity;
  title: string;
  created_by_id: number;
  related_case_id?: number | null;
  description?: string | null;
  assignee_dev_id?: number | null;
  estimated_hours?: number | null;
  metadata?: Record<string, unknown> | null;
}

/** AI Bug Fix 智能体配置。 */
export interface BugFixAgent {
  name: string;
  agent_type: "llm" | "cli";
  available: boolean;
  label: string;
  description: string;
}

/** POST /api/tasks/{id}/ai-fix 响应。 */
export interface AiBugFixResponse {
  ai_run_id: number;
  agent_name: string;
  has_git: boolean;
}

/** GET /api/version-summaries/{version_id} 返回的 VersionTestSummary。 */
export interface VersionTestSummary {
  id: number;
  version_id: number;
  total_requirements: number;
  total_tasks: number;
  total_test_cases: number;
  passed: number;
  failed: number;
  blocked: number;
  total_bugs: number;
  p0_bugs: number;
  p1_bugs: number;
  p2_bugs: number;
  p3_bugs: number;
  first_pass_rate?: number | null;
  avg_fix_time_hours?: number | null;
  test_coverage?: number | null;
  payload: {
    requirement_ids?: number[];
    task_ids?: number[];
    bug_ids?: number[];
    test_case_ids?: number[];
  };
  generated_at?: string | null;
}

/** 用例最近一次执行的聚合状态。后端 _latest_step_run_map 的优先级：
 *  error > failed > broken > skipped > passed。`pending` 在 latest_run=null
 *  时由前端兜底（CasesTab 过滤器需要这个值）。 */
export type CaseRunStatus =
  | "passed"
  | "failed"
  | "broken"
  | "error"
  | "skipped"
  | "pending";

/** GET /api/project-versions/{vid}/cases 返回的 item。 */
export interface VersionCase {
  id: number;
  name: string;
  case_type: CaseType;
  module_id: number;
  module_name: string;
  sort_order: number;
  latest_run: {
    status: CaseRunStatus;
    report_id: number;
    executed_at: string | null;
  } | null;
}

/** ProjectVersion.release_notes 是 JSON 字符串，结构如下；ArchiveTab 解析用。
 *  解析失败时降级把整段当 notes 显示。 */
export interface VersionReleaseNotes {
  sql: string;
  config: string;
  commands: string;
  notes: string;
}

/** Bug type 桶携带 by_severity；其它 type 只有 by_status。 */
export interface VersionTaskBucket {
  total: number;
  by_status: Record<TaskStatus, number>;
  by_severity?: Record<BugSeverity, number>;
}

/** GET /api/project-versions/{version_id}/board 返回值。
 *  requirements_by_status 含 4 个系统态 key + "unassigned"（system_status 为 null 时落桶）。 */
export interface VersionBoard {
  version: ProjectVersion;
  requirements_by_status: Record<string, Requirement[]>;
  task_counts_by_type: Record<TaskType, VersionTaskBucket>;
}

// =============================================================================
// M6：AI 模型配置 + 需求分析文档
// =============================================================================

export type AiProvider =
  | "openai"
  | "anthropic"
  | "ollama"
  | "deepseek"
  | "azure"
  | "custom";

export interface AiModelConfig {
  name: string;
  provider: AiProvider | string;
  model: string;
  base_url?: string | null;
  api_key?: string | null;
  supports_vision: boolean;
  is_default: boolean;
  enabled: boolean;
  extra: Record<string, unknown>;
}

export type AiModelConfigUpsert = Omit<AiModelConfig, "name">;

export interface AiModelTestResult {
  ok: boolean;
  latency_ms: number;
  sample: string;
  error?: string | null;
}

export interface AnalysisDocument {
  id: number;
  requirement_id: number;
  ai_run_id?: number | null;
  title: string;
  current_markdown?: string;
  current_version: number;
  model_label?: string | null;
  created_by_id?: number | null;
  created_by_label?: string;
  created_at?: string | null;
  updated_at?: string | null;
  ai_run?: {
    id: number;
    status: AiRunStatus;
    error?: string | null;
    tokens_in?: number | null;
    tokens_out?: number | null;
  };
}

export interface AnalysisVersion {
  id: number;
  document_id: number;
  version_no: number;
  markdown?: string;
  change_summary?: string | null;
  author_id?: number | null;
  author_label?: string;
  is_ai_generated: boolean;
  created_at?: string | null;
}

export interface AnalysisTriggerResponse {
  runs: Array<{
    run_id: number;
    model_name: string;
    model_label: string;
  }>;
}

export interface AnalysisDiffResponse {
  before: AnalysisVersion;
  after: AnalysisVersion;
}

// =============================================================================
// M7：AI 一键生成测试用例
// =============================================================================

export type AiCaseDraftStatus = "pending" | "accepted" | "rejected";
export type CaseGenerationScenarioMix =
  | "positive_only"
  | "positive_and_negative"
  | "all_scenarios";

export type CaseStepTypeHint =
  | "navigate"
  | "input"
  | "select"
  | "submit"
  | "verify"
  | "wait"
  | "cleanup"
  | "other";

export interface CaseStepTemplateItem {
  order: number;
  step_type_hint: CaseStepTypeHint | string;
  needs_ui_detail: boolean;
  hint?: string | null;
}

export interface AiCaseDraft {
  id: number;
  requirement_id: number;
  analysis_document_id?: number | null;
  ai_run_id?: number | null;
  batch_id: string;
  model_label?: string | null;

  title: string;
  preconditions?: string | null;
  steps_text?: string | null;
  expected?: string | null;
  priority: number;
  tags: string[];

  step_template: CaseStepTemplateItem[];
  needs_ui_detail: boolean;
  ui_image_refs: number[];

  status: AiCaseDraftStatus;
  committed_case_id?: number | null;

  created_at?: string | null;
  updated_at?: string | null;
}

export interface CaseGenerationTriggerPayload {
  requirement_ids: number[];
  analysis_document_id?: number | null;
  model_names: string[];
  count_per_requirement: number;
  scenario_mix: CaseGenerationScenarioMix;
  user_prompt?: string;
  ui_image_attachment_ids?: number[];
}

export interface CaseGenerationBatch {
  batch_id: string;
  requirement_id: number;
  run_id: number;
  model_name: string;
  model_label: string;
}

export interface CaseGenerationTriggerResponse {
  batches: CaseGenerationBatch[];
}

export interface AiCaseDraftUpdatePayload {
  title?: string;
  preconditions?: string | null;
  steps_text?: string | null;
  expected?: string | null;
  priority?: number;
  tags?: string[];
  step_template?: CaseStepTemplateItem[];
  needs_ui_detail?: boolean;
}

export interface CommitDraftsPayload {
  draft_ids: number[];
  target_module_id?: number | null;
}

export interface CommitDraftsResult {
  created_case_ids: number[];
  skipped: Array<{ draft_id: number; reason: string }>;
}

/** /api/ai/case-generation/runs/{id} 返回的扁平化字段。 */
export interface CaseGenerationRun extends AiRun {
  batch_id?: string | null;
  requirement_id?: number | null;
  model_name?: string | null;
}

/** Requirements 列表过滤参数（GET /api/requirements）。 */
export interface RequirementListFilters {
  status?: RequirementStatus;
  source?: RequirementSource;
  version_id?: number;
  system_status?: RequirementSystemStatus;
  business_status?: RequirementBusinessStatus;
  assignee_pm_id?: number;
  module_id?: number;
  /** M5：返回树形（只列顶层 + children），默认扁平 */
  tree?: boolean;
}

// =============================================================================
// 全局任务看板 —— GET /api/tasks/in-progress
// =============================================================================

/** 进行中的异步任务（AI + 执行 + 系统），由 task_registry 聚合生成。 */
export interface InProgressTask {
  type_key: string;
  type_label: string;
  category: string;       // "ai" | "execution" | "system"
  icon: string;           // lucide 图标名
  id: number;
  name: string;
  status: string;
  project_id: number | null;
  project_name: string | null;
  started_at: string | null;
  detail_url: string;
}
