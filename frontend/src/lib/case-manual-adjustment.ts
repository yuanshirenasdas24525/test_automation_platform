import type { TestCaseCreate } from "@/types/domain";

export const MANUAL_ADJUSTMENT_TAG = "需人工调整";
export const CASE_LOCK_TAG = "人工锁定";

/** 用户手动锁定的用例:禁止多选、显示"人工锁定"标记(存 generation_metadata.manual_locked)。 */
export function isCaseLocked(value: {
  generation_metadata?: Record<string, unknown> | null;
}): boolean {
  const metadata = value.generation_metadata;
  return (
    !!metadata && typeof metadata === "object" && metadata.manual_locked === true
  );
}

/** 读取锁定备注（generation_metadata.manual_lock_note）。 */
export function lockNote(value: {
  generation_metadata?: Record<string, unknown> | null;
}): string {
  const metadata = value.generation_metadata;
  if (!metadata || typeof metadata !== "object") return "";
  const note = metadata.manual_lock_note;
  return typeof note === "string" ? note : "";
}

type ManualAdjustmentCarrier = {
  generation_metadata?: Record<string, unknown> | null;
};

export function manualAdjustmentInfo(value: ManualAdjustmentCarrier): {
  pending: boolean;
  resolved: boolean;
  reasons: string[];
} {
  const metadata = value.generation_metadata;
  if (!metadata || typeof metadata !== "object") {
    return { pending: false, resolved: false, reasons: [] };
  }
  const rawReasons = metadata.manual_adjustment_reasons;
  const reasons = Array.isArray(rawReasons)
    ? rawReasons.map(String).map((item) => item.trim()).filter(Boolean)
    : [];
  return {
    pending: metadata.needs_manual_adjustment === true,
    resolved:
      metadata.needs_manual_adjustment === false &&
      metadata.manual_adjustment_status === "resolved",
    reasons,
  };
}

export function markForManualAdjustment(
  payload: TestCaseCreate,
  reasons: string[],
): TestCaseCreate {
  const normalizedReasons = reasons.map((item) => item.trim()).filter(Boolean);
  const oldMetadata = payload.generation_metadata ?? {};
  const oldPreflight =
    oldMetadata.preflight && typeof oldMetadata.preflight === "object"
      ? oldMetadata.preflight as Record<string, unknown>
      : null;
  const preflight = oldPreflight ?? {
    passed: false,
    errors: normalizedReasons,
  };
  return {
    ...payload,
    skip: true,
    source: "ai_interface",
    tags: Array.from(new Set([...(payload.tags ?? []), MANUAL_ADJUSTMENT_TAG])),
    generation_metadata: {
      ...oldMetadata,
      preflight,
      needs_manual_adjustment: true,
      manual_adjustment_status: "pending",
      manual_adjustment_reasons: normalizedReasons.length
        ? normalizedReasons
        : ["生成期校验未通过，请人工核对请求、依赖变量和断言"],
    },
  };
}

export function resolveManualAdjustment(payload: TestCaseCreate): TestCaseCreate {
  const oldMetadata = payload.generation_metadata ?? {};
  const oldPreflight =
    oldMetadata.preflight && typeof oldMetadata.preflight === "object"
      ? oldMetadata.preflight as Record<string, unknown>
      : {};
  return {
    ...payload,
    skip: false,
    tags: (payload.tags ?? []).filter((tag) => tag !== MANUAL_ADJUSTMENT_TAG),
    generation_metadata: {
      ...oldMetadata,
      preflight: { ...oldPreflight, manual_override: true },
      needs_manual_adjustment: false,
      manual_adjustment_status: "resolved",
      manual_adjustment_resolved_at: new Date().toISOString(),
    },
  };
}
