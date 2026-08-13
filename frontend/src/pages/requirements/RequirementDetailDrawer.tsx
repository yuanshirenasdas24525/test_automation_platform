import { useMemo, useState } from "react";
import { Pencil, Calendar, Users, Paperclip, CheckSquare, Link2, History as HistoryIcon, RotateCcw } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SideDrawer } from "@/components/ui/side-drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { AttachmentList } from "@/components/attachments/AttachmentList";
import { PriorityBadge } from "@/components/badges/PriorityBadge";
import { RequirementStatusBadge } from "@/components/badges/RequirementStatusBadge";
import { RichTextViewer } from "@/components/editor/RichTextViewer";
import { ApiError, requirementsApi, usersApi } from "@/lib/api";
import type { Requirement, RequirementEditHistory } from "@/types/domain";

interface Props {
  req: Requirement | null;
  open: boolean;
  onClose: () => void;
  onEdit: (req: Requirement) => void;
  onViewRequirement: (reqId: number) => void;
  moduleNames: Map<number, string>;
  versionNames: Map<number, string>;
}

export function RequirementDetailDrawer({ req, open, onClose, onEdit, onViewRequirement, moduleNames, versionNames }: Props) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const queryClient = useQueryClient();

  const historyQuery = useQuery({
    queryKey: ["requirementHistory", req?.id],
    queryFn: () => requirementsApi.getHistory(req!.id),
    enabled: open && !!req,
    staleTime: 10_000,
  });

  // 编号 → 名称 映射：依赖需求 / 协作成员 / 编辑历史 都用它把编号换成名称
  const reqNamesQuery = useQuery({
    queryKey: ["requirementNames", req?.project_id],
    queryFn: () => requirementsApi.list(req!.project_id),
    enabled: open && !!req,
    staleTime: 30_000,
  });
  const reqNames = useMemo(() => {
    const m = new Map<number, string>();
    (reqNamesQuery.data ?? []).forEach((r) => m.set(r.id, r.title));
    return m;
  }, [reqNamesQuery.data]);

  const usersQuery = useQuery({
    queryKey: ["allUsers"],
    queryFn: () => usersApi.list(),
    enabled: open,
    staleTime: 60_000,
  });
  const userNames = useMemo(() => {
    const m = new Map<number, string>();
    (usersQuery.data ?? []).forEach((u) => m.set(u.id, u.full_name || u.username));
    return m;
  }, [usersQuery.data]);

  if (!req) return null;

  return (
    <>
      <SideDrawer
        open={open}
        onClose={onClose}
        storageKey="requirement-detail-drawer-width"
        defaultWidth={720}
        minWidth={560}
        closeOnOutside={false}
        title={
          <>
            <span className="shrink-0 font-mono text-xs text-muted-foreground">REQ-{req.id}</span>
            <span className="truncate">{req.title}</span>
          </>
        }
        footer={
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => onEdit(req)}>
              <Pencil className="h-3.5 w-3.5 mr-1" />编辑
            </Button>
            <Button size="sm" variant="outline" onClick={() => setHistoryOpen(true)}>
              <HistoryIcon className="h-3.5 w-3.5 mr-1" />编辑历史
            </Button>
          </div>
        }
      >
        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          {/* 基本信息 */}
          <Section icon={<InfoIcon />} title="基本信息">
            <InfoGrid>
              <InfoField label="优先级">
                <PriorityBadge priority={req.priority} />
              </InfoField>
              <InfoField label="状态">
                {req.system_status ? (
                  <RequirementStatusBadge status={req.system_status} />
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </InfoField>
              <InfoField label="模块">
                {req.module_id && moduleNames.has(req.module_id) ? (
                  <span className="text-xs">{moduleNames.get(req.module_id)}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </InfoField>
              <InfoField label="关联迭代">
                {req.version_id && versionNames.has(req.version_id) ? (
                  <span className="text-xs">{versionNames.get(req.version_id)}</span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </InfoField>
              <InfoField label="来源">
                {req.source === "ai_generated" ? (
                  <span className="text-xs bg-violet-50 text-violet-700 px-1.5 py-0.5 rounded">
                    AI 生成
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">手动</span>
                )}
              </InfoField>
              <InfoField label="业务状态">
                {req.business_status ? (
                  <span className="text-xs rounded border px-1.5 py-0.5">
                    {req.business_status}
                  </span>
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </InfoField>
            </InfoGrid>
            {req.tags && req.tags.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1">
                {req.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </Section>

          <Hr />

          {/* 时间信息 */}
          <Section icon={<Calendar className="h-4 w-4" />} title="时间">
            <InfoGrid>
              <InfoField label="预计开始">
                {req.planned_start_at?.slice(0, 10) ?? "—"}
              </InfoField>
              <InfoField label="预计完成">
                {req.planned_end_at?.slice(0, 10) ?? "—"}
              </InfoField>
              <InfoField label="创建时间">
                {req.created_at?.replace("T", " ").slice(0, 16) ?? "—"}
              </InfoField>
              <InfoField label="更新时间">
                {req.updated_at?.replace("T", " ").slice(0, 16) ?? "—"}
              </InfoField>
            </InfoGrid>
          </Section>

          <Hr />

          {/* 描述 */}
          <Section icon={<FileTextIcon />} title="描述">
            <RichTextViewer source={req.description} />
          </Section>

          <Hr />

          {/* 验收标准 */}
          {req.acceptance_criteria && req.acceptance_criteria.length > 0 && (
            <>
              <Section icon={<CheckSquare className="h-4 w-4" />} title="验收标准">
                <ul className="space-y-1.5">
                  {req.acceptance_criteria.map((ac, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <span className="mt-0.5 text-xs text-muted-foreground">
                        {i + 1}.
                      </span>
                      <RichTextViewer source={ac} />
                    </li>
                  ))}
                </ul>
              </Section>
              <Hr />
            </>
          )}

          {/* 依赖需求 */}
          {req.depends_on && req.depends_on.length > 0 && (
            <>
              <Section icon={<Link2 className="h-4 w-4" />} title="依赖需求">
                <div className="flex flex-wrap gap-1">
                  {req.depends_on.map((did) => (
                    <button
                      key={did}
                      type="button"
                      onClick={() => onViewRequirement(did)}
                      title={`REQ-${did}`}
                      className="rounded border px-1.5 py-0.5 text-xs text-blue-600 hover:bg-blue-50 hover:underline cursor-pointer"
                    >
                      {reqNames.get(did) ?? `REQ-${did}`}
                    </button>
                  ))}
                </div>
              </Section>
              <Hr />
            </>
          )}

          {/* 协作成员 */}
          {req.assignees
            && (["dev", "test", "pm", "ui"] as const).some(
              (r) => (req.assignees?.[r]?.length ?? 0) > 0,
            ) && (
            <>
              <Section icon={<Users className="h-4 w-4" />} title="协作">
                <div className="grid grid-cols-2 gap-3 text-sm">
                  {(["dev", "test", "pm", "ui"] as const).map((role) => {
                    const ids = req.assignees?.[role];
                    if (!ids || ids.length === 0) return null;
                    return (
                      <div key={role}>
                        <span className="text-xs text-muted-foreground">
                          {ROLE_LABEL[role]}
                        </span>
                        <div className="mt-0.5 space-y-0.5">
                          {ids.map((uid) => (
                            <span
                              key={uid}
                              title={`#${uid}`}
                              className="block text-xs rounded bg-secondary px-1.5 py-0.5"
                            >
                              {userNames.get(uid) ?? `#${uid}`}
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Section>
              <Hr />
            </>
          )}

          {/* 附件 */}
          <Section icon={<Paperclip className="h-4 w-4" />} title="附件">
            <AttachmentList requirementId={req.id} />
          </Section>
        </div>
      </SideDrawer>

      {/* 编辑历史弹窗 */}
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-lg max-h-[70vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>编辑历史</DialogTitle>
          </DialogHeader>
          <HistoryTimeline
            history={historyQuery.data ?? []}
            loading={historyQuery.isLoading}
            userNames={userNames}
            reqNames={reqNames}
            onChanged={() => {
              historyQuery.refetch();
              queryClient.invalidateQueries({ queryKey: ["requirements"] });
              queryClient.invalidateQueries({ queryKey: ["requirementHistory", req.id] });
            }}
            onCurrentDeleted={onClose}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

const ROLE_LABEL: Record<string, string> = {
  dev: "开发",
  test: "测试",
  pm: "产品",
  ui: "UI",
};

// ---------------------------------------------------------------------------
// 子组件
// ---------------------------------------------------------------------------

function Section({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground/80">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function InfoGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-4 gap-y-2">{children}</div>;
}

function InfoField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-xs text-muted-foreground w-14 shrink-0">
        {label}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

function Hr() {
  return <hr className="border-border/60" />;
}

function HistoryTimeline({
  history,
  loading,
  userNames,
  reqNames,
  onChanged,
  onCurrentDeleted,
}: {
  history: RequirementEditHistory[];
  loading: boolean;
  userNames: Map<number, string>;
  reqNames: Map<number, string>;
  onChanged: () => void;
  onCurrentDeleted: () => void;
}) {
  const [fieldPicker, setFieldPicker] = useState<RequirementEditHistory | null>(null);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set());

  const rollbackMutation = useMutation({
    mutationFn: ({
      entry,
      fields,
      force,
      fullBatch,
    }: {
      entry: RequirementEditHistory;
      fields?: string[];
      force?: boolean;
      fullBatch?: boolean;
    }) => {
      if (!entry.batch_id) throw new Error("这条记录不支持回滚");
      return requirementsApi.rollbackHistory(entry.batch_id, {
        mode: fullBatch ? "full" : fields && fields.length > 0 ? "fields" : "partial",
        event_ids: fullBatch ? undefined : [entry.id],
        fields: fields && fields.length > 0 ? { [entry.id]: fields } : undefined,
        force,
      });
    },
    onSuccess: (_data, vars) => {
      toast.success("已回滚");
      setFieldPicker(null);
      setSelectedFields(new Set());
      onChanged();
      if (vars.entry.action === "create" && !vars.fields?.length) {
        onCurrentDeleted();
      }
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 409) {
        const ok = window.confirm("这条记录之后又发生过修改，是否强制回滚？");
        if (ok) {
          rollbackMutation.mutate({ ...vars, force: true });
        }
        return;
      }
      toast.error(err instanceof Error ? err.message : "回滚失败");
    },
  });

  if (loading) {
    return <Skeleton className="h-20 w-full" />;
  }
  if (history.length === 0) {
    return <p className="text-xs text-muted-foreground">暂无编辑记录</p>;
  }
  return (
    <div className="relative pl-5 border-l border-border/60 space-y-4">
      {history.map((entry) => (
        <div key={entry.id} className="relative">
          <div className="absolute -left-[21px] top-1.5 w-2 h-2 rounded-full bg-muted-foreground/40 ring-2 ring-background" />
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>
                {entry.created_at?.replace("T", " ").slice(0, 16) ?? "—"}
              </span>
              {entry.edited_by_id && (
                <span className="rounded bg-secondary px-1 py-0.5">
                  {userNames.get(entry.edited_by_id) ?? `#${entry.edited_by_id}`}
                </span>
              )}
            </div>
            {entry.change_summary && (
              <p className="text-sm font-medium">{entry.change_summary}</p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {actionLabel(entry.action)}
              </span>
              {entry.rollback_status && entry.rollback_status !== "none" ? (
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  已回滚
                </span>
              ) : entry.rollback_available ? (
                <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
                  可回滚
                </span>
              ) : (
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  仅审计
                </span>
              )}
            </div>
            {entry.changes && entry.changes.length > 0 && (
              <div className="space-y-1">
                {entry.changes.map((ch, i) => (
                  <div key={i} className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground/80">
                      {ch.label}
                    </span>
                    ：{" "}
                    <span className="line-through text-red-500">
                      {_prettyVal(ch.old, ch.field, userNames, reqNames)}
                    </span>
                    {" → "}
                    <span className="text-green-600">
                      {_prettyVal(ch.new, ch.field, userNames, reqNames)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {entry.rollback_available && entry.batch_id && (
              <div className="flex items-center gap-2 pt-1">
                {entry.action === "update" && entry.changes.length > 1 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2 text-xs"
                    onClick={() => {
                      setFieldPicker(entry);
                      setSelectedFields(new Set(entry.changes.map((ch) => ch.field)));
                    }}
                  >
                    <RotateCcw className="mr-1 h-3 w-3" />
                    选择字段回滚
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 px-2 text-xs"
                  disabled={rollbackMutation.isPending}
                  onClick={() => {
                    const ok = window.confirm(
                      entry.action === "create"
                        ? "回滚新增会删除这条需求，确认继续？"
                        : "确认回滚这条编辑记录？",
                    );
                    if (ok) rollbackMutation.mutate({ entry });
                  }}
                >
                  <RotateCcw className="mr-1 h-3 w-3" />
                  整条回滚
                </Button>
                {history.filter((item) => item.batch_id === entry.batch_id).length > 1 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 px-2 text-xs"
                    disabled={rollbackMutation.isPending}
                    onClick={() => {
                      const ok = window.confirm("确认回滚这一次批量操作？");
                      if (ok) rollbackMutation.mutate({ entry, fullBatch: true });
                    }}
                  >
                    <RotateCcw className="mr-1 h-3 w-3" />
                    整次回滚
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
      <Dialog open={!!fieldPicker} onOpenChange={(v) => !v && setFieldPicker(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>选择字段回滚</DialogTitle>
          </DialogHeader>
          {fieldPicker && (
            <div className="space-y-3">
              <div className="space-y-2">
                {fieldPicker.changes.map((ch) => {
                  const checked = selectedFields.has(ch.field);
                  return (
                    <label key={ch.field} className="flex gap-2 rounded border p-2 text-xs">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={checked}
                        onChange={(e) => {
                          setSelectedFields((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(ch.field);
                            else next.delete(ch.field);
                            return next;
                          });
                        }}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium text-foreground">{ch.label}</span>
                        <span className="block truncate text-muted-foreground">
                          当前：{_prettyVal(ch.new, ch.field)}
                        </span>
                        <span className="block truncate text-emerald-700">
                          回滚到：{_prettyVal(ch.old, ch.field)}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setFieldPicker(null)}>
                  取消
                </Button>
                <Button
                  disabled={selectedFields.size === 0 || rollbackMutation.isPending}
                  onClick={() => {
                    rollbackMutation.mutate({
                      entry: fieldPicker,
                      fields: [...selectedFields],
                    });
                  }}
                >
                  确认回滚
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function actionLabel(action?: string): string {
  if (action === "create") return "新增";
  if (action === "update") return "修改";
  if (action === "delete") return "删除";
  if (action === "mixed") return "批量";
  return "修改";
}

function stripHtml(html: string): string {
  if (!html) return "";
  // 描述可能是正常 HTML，也可能是被转义存储的 HTML（&lt;p&gt;…）。
  // 反复「解码实体 + 去标签」直到稳定，确保两种情况都还原成纯文本。
  let text = html;
  for (let i = 0; i < 3; i++) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    const stripped = (doc.body.textContent || "").replace(/<[^>]+>/g, "");
    if (stripped === text) break;
    text = stripped;
  }
  return text.replace(/\s+/g, " ").trim();
}

function _prettyVal(
  v: unknown,
  field: string,
  userNames?: Map<number, string>,
  reqNames?: Map<number, string>,
): string {
  if (v === null || v === undefined) return "（无）";
  const userName = (id: unknown) =>
    userNames?.get(Number(id)) ?? `#${id}`;
  const reqName = (id: unknown) =>
    reqNames?.get(Number(id)) ?? `REQ-${id}`;

  if (field === "description") {
    const text = stripHtml(String(v));
    return text.length > 80 ? text.slice(0, 80) + "…" : text;
  }
  // 协作成员：{dev:[],test:[],pm:[],ui:[]} → 角色:姓名
  if (field === "assignees") {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const obj = v as Record<string, unknown>;
      const parts: string[] = [];
      for (const role of ["dev", "test", "pm", "ui"] as const) {
        const ids = obj[role];
        if (Array.isArray(ids) && ids.length > 0) {
          parts.push(`${ROLE_LABEL[role]}: ${ids.map(userName).join("、")}`);
        }
      }
      return parts.length ? parts.join("；") : "（无）";
    }
    if (Array.isArray(v)) return v.length ? v.map(userName).join("、") : "（空）";
    return String(v);
  }
  // 产品负责人等单个用户 id 字段
  if (field === "assignee_pm_id" || field.endsWith("_user_id")) {
    return userName(v);
  }
  // 依赖需求：需求 id 数组 → 需求名称
  if (field === "depends_on") {
    if (Array.isArray(v)) return v.length ? v.map(reqName).join("、") : "（空）";
    return reqName(v);
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return "（空）";
    return v.map(String).join(", ");
  }
  return String(v);
}

// ---------------------------------------------------------------------------
// inline icons
// ---------------------------------------------------------------------------
function InfoIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className="h-4 w-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );
}

function FileTextIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      className="h-4 w-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}
