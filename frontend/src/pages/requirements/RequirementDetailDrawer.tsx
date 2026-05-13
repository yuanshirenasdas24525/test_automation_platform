import { useEffect, useState } from "react";
import { X, Pencil, Calendar, Users, Paperclip, CheckSquare, Link2, History as HistoryIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { AttachmentList } from "@/components/attachments/AttachmentList";
import { PriorityBadge } from "@/components/badges/PriorityBadge";
import { RequirementStatusBadge } from "@/components/badges/RequirementStatusBadge";
import { RichTextViewer } from "@/components/editor/RichTextViewer";
import { requirementsApi, usersApi } from "@/lib/api";
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

  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  const historyQuery = useQuery({
    queryKey: ["requirementHistory", req?.id],
    queryFn: () => requirementsApi.getHistory(req!.id),
    enabled: open && !!req,
    staleTime: 10_000,
  });

  if (!req || !open) return null;

  return (
    <div className="fixed inset-0 z-50 flex">
      <div
        className="flex-1 bg-black/30 transition-opacity"
        onClick={onClose}
        aria-hidden
      />
      <div className="relative w-full max-w-[720px] bg-background shadow-2xl animate-slide-in-right overflow-y-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-background px-6 py-4">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-xs font-mono text-muted-foreground shrink-0">
              REQ-{req.id}
            </span>
            <h2 className="truncate text-base font-semibold">{req.title}</h2>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} className="shrink-0">
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="space-y-6 px-6 py-5">
          {/* 操作按钮 */}
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => onEdit(req)}>
              <Pencil className="h-3.5 w-3.5 mr-1" />
              编辑
            </Button>
            <Button size="sm" variant="outline" onClick={() => setHistoryOpen(true)}>
              <HistoryIcon className="h-3.5 w-3.5 mr-1" />
              编辑历史
            </Button>
          </div>

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
                      className="rounded border px-1.5 py-0.5 text-xs font-mono text-blue-600 hover:bg-blue-50 hover:underline cursor-pointer"
                    >
                      REQ-{did}
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
                              className="block text-xs rounded bg-secondary px-1.5 py-0.5"
                            >
                              #{uid}
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
      </div>

      {/* 编辑历史弹窗 */}
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="max-w-lg max-h-[70vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>编辑历史</DialogTitle>
          </DialogHeader>
          <HistoryTimeline history={historyQuery.data ?? []} loading={historyQuery.isLoading} />
        </DialogContent>
      </Dialog>
    </div>
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
}: {
  history: RequirementEditHistory[];
  loading: boolean;
}) {
  const [userNames, setUserNames] = useState<Map<number, string>>(new Map());

  useEffect(() => {
    const ids = history
      .map((e) => e.edited_by_id)
      .filter((id): id is number => id != null);
    const uniqueIds = [...new Set(ids)];
    if (uniqueIds.length === 0) return;
    Promise.all(uniqueIds.map((id) => usersApi.get(id).catch(() => null)))
      .then((users) => {
        setUserNames((prev) => {
          const next = new Map(prev);
          users.forEach((u, i) => {
            if (u) next.set(uniqueIds[i], u.full_name || u.username);
          });
          return next;
        });
      });
  }, [history]);

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
            {entry.changes && entry.changes.length > 0 && (
              <div className="space-y-1">
                {entry.changes.map((ch, i) => (
                  <div key={i} className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground/80">
                      {ch.label}
                    </span>
                    ：{" "}
                    <span className="line-through text-red-500">
                      {_prettyVal(ch.old, ch.field)}
                    </span>
                    {" → "}
                    <span className="text-green-600">
                      {_prettyVal(ch.new, ch.field)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function stripHtml(html: string): string {
  if (!html) return "";
  const doc = new DOMParser().parseFromString(html, "text/html");
  return (doc.body.textContent || "").replace(/\s+/g, " ").trim();
}

function _prettyVal(v: unknown, field: string): string {
  if (v === null || v === undefined) return "（无）";
  if (field === "description") {
    const text = stripHtml(String(v));
    return text.length > 80 ? text.slice(0, 80) + "…" : text;
  }
  if (field === "assignees") {
    return String(v);
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
