import type {
  CaseType,
  FunctionalCase,
  FunctionalCaseCreate,
  TestCaseCreate,
  TestCaseDetail,
} from "@/types/domain";
import type { StepPlatformGroup } from "@/lib/case-clipboard";

/**
 * 步骤 step_type → 平台组。通用步骤（sleep/assert 等）返回 null，
 * 交给 resolveCopyStepGroup 按来源用例归组。
 */
export function stepPlatformGroupOf(stepType: string): StepPlatformGroup | null {
  if (stepType.startsWith("web_")) return "web";
  if (stepType.startsWith("app_")) return "app";
  if (stepType === "http_request") return "api";
  return null;
}

/** 用例 case_type → 步骤平台组。api / mixed / functional 归 api。 */
export function caseGroupOf(caseType: CaseType): StepPlatformGroup {
  if (caseType === "web") return "web";
  if (caseType === "android" || caseType === "ios") return "app";
  return "api";
}

/** 复制某步骤时确定它的平台组：优先按 step_type，通用步骤回退到来源用例组。 */
export function resolveCopyStepGroup(stepType: string, caseType: CaseType): StepPlatformGroup {
  return stepPlatformGroupOf(stepType) ?? caseGroupOf(caseType);
}

/**
 * 剪贴板里的步骤能否粘贴进当前 category 的用例。
 * mixed 用例接受任意组；其余按组严格匹配。
 */
export function canPasteStep(group: StepPlatformGroup, caseType: CaseType): boolean {
  if (caseType === "mixed") return true;
  return caseGroupOf(caseType) === group;
}

/** 剪贴板里的用例能否粘贴进当前列表：case_type 严格相等（各类型互相隔离）。 */
export function canPasteCase(clipCaseType: CaseType, listCaseType: CaseType): boolean {
  return clipCaseType === listCaseType;
}

/** 生成不与现有名冲突的副本名："X_副本"、"X_副本2"、"X_副本3"… */
export function dedupeCopyName(baseName: string, existing: Set<string>): string {
  const root = `${baseName}_副本`;
  if (!existing.has(root)) return root;
  let n = 2;
  while (existing.has(`${root}${n}`)) n += 1;
  return `${root}${n}`;
}

/**
 * 由用例详情快照组装「新建副本」的 payload：
 * 去掉 id / sort_order（排序另由 reorder 处理），重置每个 step 的 id，改名。
 * case_type / priority / tags / variables / description 等随 detail 原样保留。
 */
export function buildCaseCopyPayload(
  detail: TestCaseDetail,
  moduleId: number,
  name: string,
): TestCaseCreate {
  const { id: _id, sort_order: _sortOrder, steps, ...rest } = detail;
  return {
    ...rest,
    module_id: moduleId,
    name,
    steps: (steps ?? []).map((s, idx) => ({ ...s, id: null, step_order: idx })),
  };
}

/**
 * 由功能用例快照组装「新建副本」的 payload。功能用例内容在 functional_spec
 * (前置条件/步骤/预期) 这个 JSON 列里，走 functionalCasesApi，不能用上面的通用 payload。
 * 去掉 id / sort_order / latest_run，深拷贝 functional_spec，改名。
 */
export function buildFunctionalCopyPayload(
  source: FunctionalCase,
  moduleId: number,
  name: string,
): FunctionalCaseCreate {
  return {
    module_id: moduleId,
    name,
    description: source.description ?? null,
    skip: source.skip,
    priority: source.priority ?? null,
    tags: source.tags ?? [],
    functional_spec: source.functional_spec
      ? structuredClone(source.functional_spec)
      : null,
  };
}
