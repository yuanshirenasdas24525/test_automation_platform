/**
 * M7 · 单条 AI 用例草稿展示 + 内联编辑。
 *
 * 字段：title / preconditions / steps_text / expected / priority / tags
 * 行为：
 *   - 顶部 checkbox（commit 时勾选）
 *   - 右上角 "needs_ui_detail" 标签（任一 step 需要 UI 细节就高亮）
 *   - 展开 / 折叠：折叠态只显示标题 + meta；展开后才看见步骤
 *   - 编辑按钮 → 进入编辑态；保存调 onSave；取消恢复
 *   - 删除（逻辑删）按钮 → 调 onReject
 *
 * 控件级 UI 细节缺失时（needs_ui_detail=true），用一条提示条提醒 PM 上传截图重跑。
 */
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Pencil,
  Save,
  Trash2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { AiCaseDraft, AiCaseDraftUpdatePayload } from "@/types/domain";


interface Props {
  draft: AiCaseDraft;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  onSave: (patch: AiCaseDraftUpdatePayload) => Promise<void> | void;
  onReject: () => Promise<void> | void;
  saving?: boolean;
  rejecting?: boolean;
}


const PRIORITY_LABELS = ["P0", "P1", "P2", "P3"];


export function CaseDraftRow({
  draft,
  checked,
  onCheckedChange,
  onSave,
  onReject,
  saving,
  rejecting,
}: Props) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(draft.title);
  const [preconditions, setPreconditions] = useState(draft.preconditions ?? "");
  const [stepsText, setStepsText] = useState(draft.steps_text ?? "");
  const [expected, setExpected] = useState(draft.expected ?? "");
  const [priority, setPriority] = useState(draft.priority);

  // 草稿被父组件 refetch 替换后同步本地 state（仅非编辑态）
  useEffect(() => {
    if (editing) return;
    setTitle(draft.title);
    setPreconditions(draft.preconditions ?? "");
    setStepsText(draft.steps_text ?? "");
    setExpected(draft.expected ?? "");
    setPriority(draft.priority);
  }, [
    editing,
    draft.title,
    draft.preconditions,
    draft.steps_text,
    draft.expected,
    draft.priority,
  ]);

  const disabled = draft.status !== "pending";

  const reset = () => {
    setTitle(draft.title);
    setPreconditions(draft.preconditions ?? "");
    setStepsText(draft.steps_text ?? "");
    setExpected(draft.expected ?? "");
    setPriority(draft.priority);
  };

  const handleSave = async () => {
    await onSave({
      title: title.trim() || draft.title,
      preconditions: preconditions || null,
      steps_text: stepsText || null,
      expected: expected || null,
      priority,
    });
    setEditing(false);
  };

  return (
    <div
      className={[
        "rounded border bg-card p-3 transition",
        disabled ? "opacity-60" : "",
        checked && !disabled ? "border-violet-500/60" : "border-border",
      ].join(" ")}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={checked && !disabled}
          disabled={disabled}
          onChange={(e) => onCheckedChange(e.target.checked)}
        />

        <div className="min-w-0 flex-1">
          {editing ? (
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="用例标题"
            />
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="text-left text-sm font-medium hover:underline"
                onClick={() => setOpen((v) => !v)}
              >
                {open ? (
                  <ChevronUp className="mr-0.5 inline h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="mr-0.5 inline h-3.5 w-3.5" />
                )}
                {draft.title}
              </button>
              <PriorityChip priority={draft.priority} />
              {draft.needs_ui_detail ? (
                <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">
                  <AlertTriangle className="h-3 w-3" /> 需 UI 细节
                </span>
              ) : null}
              {draft.tags?.length ? (
                <span className="text-xs text-muted-foreground">
                  {draft.tags.map((t) => `#${t}`).join(" ")}
                </span>
              ) : null}
              {draft.status !== "pending" ? (
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                  {draft.status}
                  {draft.committed_case_id
                    ? ` · case#${draft.committed_case_id}`
                    : ""}
                </span>
              ) : null}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1">
          {disabled ? null : editing ? (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  reset();
                  setEditing(false);
                }}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
              <Button size="sm" onClick={handleSave} disabled={saving}>
                {saving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Save className="h-3.5 w-3.5" />
                )}
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setOpen(true);
                  setEditing(true);
                }}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onReject}
                disabled={rejecting}
                title="弃用该草稿"
              >
                {rejecting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5 text-rose-500" />
                )}
              </Button>
            </>
          )}
        </div>
      </div>

      {(open || editing) && (
        <div className="mt-3 space-y-2">
          {editing ? (
            <>
              <Field label="前置条件">
                <Textarea
                  rows={2}
                  value={preconditions}
                  onChange={(e) => setPreconditions(e.target.value)}
                  placeholder="1. xxx 已配置&#10;2. yyy 处于可用状态"
                />
              </Field>
              <Field label="测试步骤（按 1. 2. 3. 编号）">
                <Textarea
                  rows={5}
                  value={stepsText}
                  onChange={(e) => setStepsText(e.target.value)}
                  placeholder="1. 用户访问注册页&#10;2. 输入合法手机号"
                />
              </Field>
              <Field label="预期结果">
                <Textarea
                  rows={3}
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                />
              </Field>
              <Field label="优先级">
                <div className="flex gap-1">
                  {PRIORITY_LABELS.map((label, idx) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => setPriority(idx)}
                      className={[
                        "rounded border px-2 py-0.5 text-xs",
                        priority === idx
                          ? "border-violet-500 bg-violet-50/70"
                          : "border-border hover:bg-muted/40",
                      ].join(" ")}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </Field>
            </>
          ) : (
            <>
              {draft.preconditions ? (
                <ReadField label="前置条件" text={draft.preconditions} />
              ) : null}
              {draft.steps_text ? (
                <ReadField label="测试步骤" text={draft.steps_text} />
              ) : null}
              {draft.expected ? (
                <ReadField label="预期结果" text={draft.expected} />
              ) : null}
              {draft.needs_ui_detail ? (
                <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
                  部分步骤标记为「需 UI 细节」，建议提供 UI 截图后重新生成以获得更准确的步骤。
                </div>
              ) : null}
              {draft.model_label ? (
                <div className="text-xs text-muted-foreground">
                  模型：{draft.model_label}
                </div>
              ) : null}
            </>
          )}
        </div>
      )}
    </div>
  );
}


function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label className="text-xs">{label}</Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}


function ReadField({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <pre className="mt-0.5 whitespace-pre-wrap break-words rounded bg-muted/40 px-2 py-1 text-xs">
        {text}
      </pre>
    </div>
  );
}


function PriorityChip({ priority }: { priority: number }) {
  const label = PRIORITY_LABELS[priority] ?? `P${priority}`;
  const tone =
    priority === 0
      ? "bg-rose-100 text-rose-700"
      : priority === 1
        ? "bg-amber-100 text-amber-700"
        : "bg-slate-100 text-slate-700";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${tone}`}>{label}</span>
  );
}
