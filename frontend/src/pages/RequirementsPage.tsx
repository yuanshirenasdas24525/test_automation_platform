import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  ApiError,
  aiApi,
  projectsApi,
  requirementsApi,
} from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { AiRun, Requirement, RequirementStatus } from "@/types/domain";

/**
 * 需求管理页（项目级）。
 *
 * 两个核心入口：
 *   - 手工新建：弹 dialog，填 title/description/acceptance_criteria
 *   - AI 解析：粘贴 PRD/需求文本 → 调 /api/ai/requirement_parse 异步生成
 *     → 完成后列表自动刷新（轮询 ai_run 状态 + invalidate）
 *
 * 一条需求支持 draft → approved → archived 三态切换。
 * AI 生成的会带 source=ai_generated 徽章 + 链回到 ai_run 详情（debug 用）。
 */

const STATUS_META: Record<
  RequirementStatus,
  { label: string; tone: string; icon: React.ComponentType<{ className?: string }> }
> = {
  draft: {
    label: "草稿",
    tone: "text-amber-700 bg-amber-50 ring-amber-200",
    icon: CircleDashed,
  },
  approved: {
    label: "已确认",
    tone: "text-emerald-700 bg-emerald-50 ring-emerald-200",
    icon: CheckCircle2,
  },
  archived: {
    label: "归档",
    tone: "text-slate-600 bg-slate-100 ring-slate-200",
    icon: XCircle,
  },
};

export function RequirementsPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [editing, setEditing] = useState<
    | { mode: "create" }
    | { mode: "edit"; req: Requirement }
    | null
  >(null);
  const [aiOpen, setAiOpen] = useState(false);

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => projectsApi.get(projectId),
    enabled: Number.isFinite(projectId),
  });

  const listQuery = useQuery({
    queryKey: queryKeys.requirements(
      projectId,
      statusFilter === "all" ? undefined : { status: statusFilter },
    ),
    queryFn: () =>
      requirementsApi.list(projectId, {
        status:
          statusFilter === "all"
            ? undefined
            : (statusFilter as RequirementStatus),
      }),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError
        ? err.message
        : err instanceof Error
          ? err.message
          : "操作失败";
    toast.error(msg);
  };

  const removeMutation = useMutation({
    mutationFn: (rid: number) => requirementsApi.remove(rid),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
    },
    onError: handleError,
  });

  const updateStatus = useMutation({
    mutationFn: ({ id: rid, status }: { id: number; status: RequirementStatus }) =>
      requirementsApi.update(rid, { status }),
    onSuccess: () => invalidate(),
    onError: handleError,
  });

  if (!Number.isFinite(projectId)) {
    return <div className="p-8 text-sm text-destructive">非法的项目 ID。</div>;
  }

  const items = listQuery.data ?? [];

  return (
    <div className="space-y-4 p-6">
      {/* 顶栏 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0"
            onClick={() => navigate(`/projects/${projectId}`)}
            title="返回项目详情"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-muted-foreground text-sm">需求 ·</span>
            <span className="truncate font-medium">
              {projectQuery.data?.name ?? "…"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditing({ mode: "create" })}>
            <Plus className="h-4 w-4" />
            新建需求
          </Button>
          <Button size="sm" onClick={() => setAiOpen(true)}>
            <Sparkles className="h-4 w-4" />
            AI 解析需求
          </Button>
        </div>
      </div>

      {/* 过滤栏 */}
      <div className="flex items-center gap-2">
        <Label className="text-xs text-muted-foreground">状态</Label>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="h-8 w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部</SelectItem>
            <SelectItem value="draft">草稿</SelectItem>
            <SelectItem value="approved">已确认</SelectItem>
            <SelectItem value="archived">归档</SelectItem>
          </SelectContent>
        </Select>
        <span className="ml-2 text-xs text-muted-foreground">
          共 {items.length} 条
        </span>
      </div>

      {/* 列表 */}
      {listQuery.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            还没有需求 —— 用 AI 解析 PRD 一键导入，或手工新建
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((r) => (
            <RequirementRow
              key={r.id}
              req={r}
              onEdit={() => setEditing({ mode: "edit", req: r })}
              onDelete={() => {
                if (confirm(`删除需求"${r.title}"？`))
                  removeMutation.mutate(r.id);
              }}
              onStatusChange={(status) =>
                updateStatus.mutate({ id: r.id, status })
              }
            />
          ))}
        </div>
      )}

      <RequirementDialog
        state={editing}
        projectId={projectId}
        onClose={() => setEditing(null)}
        onDone={() => {
          invalidate();
          setEditing(null);
        }}
        onError={handleError}
      />

      <AiParseDialog
        open={aiOpen}
        projectId={projectId}
        onClose={() => setAiOpen(false)}
        onDone={() => {
          invalidate();
          setAiOpen(false);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单条需求渲染
// ---------------------------------------------------------------------------
function RequirementRow({
  req,
  onEdit,
  onDelete,
  onStatusChange,
}: {
  req: Requirement;
  onEdit: () => void;
  onDelete: () => void;
  onStatusChange: (s: RequirementStatus) => void;
}) {
  const meta = STATUS_META[req.status];
  const Icon = meta.icon;
  return (
    <Card>
      <CardContent className="flex items-start gap-3 py-3">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{req.title}</span>
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs ring-1 ring-inset",
                meta.tone,
              )}
            >
              <Icon className="h-3 w-3" />
              {meta.label}
            </span>
            {req.priority != null ? (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
                P{req.priority}
              </span>
            ) : null}
            {req.source === "ai_generated" ? (
              <span className="inline-flex items-center gap-1 rounded bg-violet-50 px-1.5 py-0.5 text-[10px] uppercase text-violet-700 ring-1 ring-inset ring-violet-200">
                <Sparkles className="h-3 w-3" />
                AI
              </span>
            ) : null}
            {req.tags?.length
              ? req.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground"
                  >
                    {t}
                  </span>
                ))
              : null}
          </div>
          {req.description ? (
            <p className="text-xs text-muted-foreground">{req.description}</p>
          ) : null}
          {req.acceptance_criteria?.length ? (
            <ul className="ml-4 list-disc text-xs text-muted-foreground">
              {req.acceptance_criteria.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Select
            value={req.status}
            onValueChange={(v) => onStatusChange(v as RequirementStatus)}
          >
            <SelectTrigger className="h-7 w-24 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="draft">草稿</SelectItem>
              <SelectItem value="approved">已确认</SelectItem>
              <SelectItem value="archived">归档</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onEdit}>
            <Pencil className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive"
            onClick={onDelete}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 手工创建/编辑对话框
// ---------------------------------------------------------------------------
function RequirementDialog({
  state,
  projectId,
  onClose,
  onDone,
  onError,
}: {
  state: { mode: "create" } | { mode: "edit"; req: Requirement } | null;
  projectId: number;
  onClose: () => void;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const isEdit = state?.mode === "edit";
  const initial = state?.mode === "edit" ? state.req : null;

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [priority, setPriority] = useState<number>(2);
  const [tagsText, setTagsText] = useState("");

  useEffect(() => {
    if (!state) return;
    if (initial) {
      setTitle(initial.title);
      setDescription(initial.description || "");
      setCriteriaText((initial.acceptance_criteria || []).join("\n"));
      setPriority(initial.priority ?? 2);
      setTagsText((initial.tags || []).join(","));
    } else {
      setTitle("");
      setDescription("");
      setCriteriaText("");
      setPriority(2);
      setTagsText("");
    }
  }, [state, initial]);

  const createMutation = useMutation({
    mutationFn: () =>
      requirementsApi.create({
        project_id: projectId,
        title: title.trim(),
        description: description.trim() || null,
        acceptance_criteria: criteriaText
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        priority,
        tags: tagsText
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      toast.success("需求已创建");
      onDone();
    },
    onError,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!initial) return Promise.reject(new Error("invalid"));
      return requirementsApi.update(initial.id, {
        title: title.trim(),
        description: description.trim() || null,
        acceptance_criteria: criteriaText
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
        priority,
        tags: tagsText
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean),
      });
    },
    onSuccess: () => {
      toast.success("已更新");
      onDone();
    },
    onError,
  });

  const submit = () => {
    if (!title.trim()) {
      toast.error("请填写标题");
      return;
    }
    if (isEdit) updateMutation.mutate();
    else createMutation.mutate();
  };

  if (!state) return null;
  const submitting = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑需求" : "新建需求"}</DialogTitle>
          <DialogDescription>
            一条需求 = 一个可被测试覆盖的功能点
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">标题</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">描述</Label>
            <Textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="一句话扩展，写明用户做什么 + 期望结果"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">验收标准（每行一条）</Label>
            <Textarea
              rows={4}
              value={criteriaText}
              onChange={(e) => setCriteriaText(e.target.value)}
              placeholder="未登录用户访问 /admin 时跳转到 /login&#10;已登录普通用户访问 /admin 时返回 403"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">优先级（0-3）</Label>
              <Input
                type="number"
                min={0}
                max={3}
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value) || 0)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">标签（逗号分隔）</Label>
              <Input
                value={tagsText}
                onChange={(e) => setTagsText(e.target.value)}
                placeholder="登录,权限"
              />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {isEdit ? "保存" : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// AI 解析对话框 V2（支持分析模式、文件上传、上下文信息展示）
// ---------------------------------------------------------------------------
function AiParseDialog({
  open,
  projectId,
  onClose,
  onDone,
}: {
  open: boolean;
  projectId: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [analysisMode, setAnalysisMode] = useState<string>("standard");
  const [inputMode, setInputMode] = useState<"text" | "file">("text");
  const [aiRunId, setAiRunId] = useState<number | null>(null);

  const submitMutation = useMutation({
    mutationFn: () => {
      const payload: any = {
        project_id: projectId,
        analysis_mode: analysisMode,
      };
      if (inputMode === "text") {
        payload.text = text.trim();
      }
      return aiApi.submitRequirementParse(payload);
    },
    onSuccess: (res) => {
      setAiRunId(res.ai_run_id);
      toast.success(`AI 任务已提交（${analysisMode === "multi_model" ? "多模型" : analysisMode === "deep" ? "深度" : analysisMode === "quick" ? "快速" : "标准"}分析）`);
    },
    onError: (err) => {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "提交失败";
      toast.error(msg);
    },
  });

  // 轮询
  const runQuery = useQuery({
    queryKey: queryKeys.aiRun(aiRunId ?? -1),
    queryFn: () => aiApi.getRun(aiRunId!),
    enabled: aiRunId !== null,
    refetchInterval: (query) => {
      const status = (query.state.data as AiRun | undefined)?.status;
      if (status === "success" || status === "failed" || status === "cancelled") {
        return false;
      }
      return 2000;
    },
  });

  const run = runQuery.data;
  const isRunning =
    run?.status === "pending" || run?.status === "running" || submitMutation.isPending;

  const reset = () => {
    setText("");
    setAiRunId(null);
    setAnalysisMode("standard");
    setInputMode("text");
  };

  useEffect(() => {
    if (run?.status === "success") {
      onDone();
    }
  }, [run?.status, onDone]);

  const modeLabels: Record<string, string> = {
    quick: "快速扫描（轻量模型，秒级完成）",
    standard: "标准分析（平衡质量与速度）",
    deep: "深度分析（旗舰模型，全面提取）",
    multi_model: "多模型集成（2-3 个模型交叉验证，最准确）",
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          reset();
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-violet-600" />
            AI 解析需求
          </DialogTitle>
          <DialogDescription>
            粘贴 PRD / 用户故事 / 需求描述，AI 会拆成可测试的需求点
            {analysisMode === "deep" ? "（含项目上下文分析）" : ""}
          </DialogDescription>
        </DialogHeader>

        {!aiRunId ? (
          <div className="space-y-3">
            {/* 分析模式选择 */}
            <div className="space-y-1">
              <Label className="text-xs">分析模式</Label>
              <Select value={analysisMode} onValueChange={setAnalysisMode}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="quick">{modeLabels.quick}</SelectItem>
                  <SelectItem value="standard">{modeLabels.standard}</SelectItem>
                  <SelectItem value="deep">{modeLabels.deep}</SelectItem>
                  <SelectItem value="multi_model">{modeLabels.multi_model}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* 输入方式 */}
            <div className="space-y-1">
              <Label className="text-xs">输入方式</Label>
              <div className="flex gap-2">
                <Button
                  variant={inputMode === "text" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setInputMode("text")}
                >
                  粘贴文本
                </Button>
                <Button
                  variant={inputMode === "file" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setInputMode("file")}
                >
                  上传文档
                </Button>
              </div>
            </div>

            {inputMode === "text" ? (
              <>
                <Textarea
                  rows={12}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="例如：&#10;用户管理模块需要支持创建 / 编辑 / 删除 / 锁定四种操作。&#10;创建用户时需要校验邮箱格式 + 密码强度。&#10;锁定后的用户登录失败次数清零。&#10;删除用户为软删除，30 天内可恢复。"
                />
                <p className="text-xs text-muted-foreground">
                  建议至少给一段完整的需求段落（≥ 10 个字）
                </p>
              </>
            ) : (
              <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
                文件上传功能在后续版本中提供（配置 file_path 路径即可）
                <br />
                <span className="text-xs">支持 PDF / DOCX / MD / TXT 格式</span>
              </div>
            )}

            {analysisMode === "multi_model" && (
              <div className="rounded bg-violet-50 p-2 text-xs text-violet-800">
                多模型集成将同时调用 2-3 个不同模型分析同一份需求，投票聚合结果，置信度更高。
                成本约为标准模式的 2-3 倍。
              </div>
            )}
          </div>
        ) : (
          <AiRunProgress run={run} analysisMode={analysisMode} />
        )}

        <DialogFooter>
          {!aiRunId ? (
            <>
              <Button variant="outline" onClick={onClose}>
                取消
              </Button>
              <Button
                onClick={() => submitMutation.mutate()}
                disabled={inputMode === "text" ? text.trim().length < 10 : false || submitMutation.isPending}
              >
                {submitMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Sparkles className="h-4 w-4" />
                )}
                开始解析
              </Button>
            </>
          ) : (
            <>
              {isRunning ? (
                <Button
                  variant="outline"
                  onClick={() => aiApi.cancelRun(aiRunId).catch(() => {})}
                >
                  取消任务
                </Button>
              ) : (
                <Button variant="outline" onClick={reset}>
                  再来一次
                </Button>
              )}
              <Button
                onClick={() => {
                  reset();
                  onClose();
                }}
              >
                关闭
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AiRunProgress({
  run,
  analysisMode,
}: {
  run?: AiRun | null;
  analysisMode?: string;
}) {
  if (!run) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载任务状态...
      </div>
    );
  }

  if (run.status === "failed") {
    return (
      <div className="space-y-2 rounded border border-destructive/40 bg-destructive/5 p-3 text-sm">
        <div className="font-medium text-destructive">AI 任务失败</div>
        <pre className="whitespace-pre-wrap text-xs text-destructive">
          {run.error}
        </pre>
      </div>
    );
  }

  if (run.status === "cancelled") {
    return <div className="text-sm text-muted-foreground">任务已取消</div>;
  }

  if (run.status !== "success") {
    const modeHint =
      analysisMode === "deep"
        ? "深度分析中，请耐心等待"
        : analysisMode === "multi_model"
          ? "多模型并行分析中（2-3 个模型）"
          : "分析中，通常 10-30 秒";
    return (
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        {modeHint}...
      </div>
    );
  }

  const out = run.output_payload as
    | {
        summary?: string;
        created_count?: number;
        requirements?: any[];
        context_items_count?: number;
        matched_contexts_count?: number;
        analysis_notes?: { topic: string; note: string }[];
        analysis_mode?: string;
      }
    | undefined;
  const requirements = out?.requirements ?? [];
  const modeLabel =
    out?.analysis_mode === "multi_model"
      ? "多模型"
      : out?.analysis_mode === "deep"
        ? "深度"
        : out?.analysis_mode === "quick"
          ? "快速"
          : "标准";

  return (
    <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
      {/* 摘要 */}
      <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-900 ring-1 ring-emerald-200">
        ✅ 已生成 <strong>{out?.created_count ?? 0}</strong> 条需求
        {out?.context_items_count != null && out.context_items_count > 0 ? (
          <span className="ml-1">
            ，提取 <strong>{out.context_items_count}</strong> 条项目知识
          </span>
        ) : null}
        {out?.matched_contexts_count != null && out.matched_contexts_count > 0 ? (
          <span className="ml-1">
            ，匹配 <strong>{out.matched_contexts_count}</strong> 条历史上下文
          </span>
        ) : null}
        <span className="ml-2 text-xs">（{modeLabel}分析）</span>
        {out?.summary ? (
          <>
            <br />
            <span className="text-xs text-emerald-700">{out.summary}</span>
          </>
        ) : null}
      </div>

      {/* Token & 成本 */}
      <div className="text-xs text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
        <span>Token：{run.tokens_in ?? 0} in / {run.tokens_out ?? 0} out</span>
        {run.cost_usd != null ? (
          <span>成本 ${run.cost_usd.toFixed(4)}</span>
        ) : null}
        <span>
          {run.provider} · {run.model}
        </span>
      </div>

      {/* 分析注释 */}
      {out?.analysis_notes?.length ? (
        <div className="rounded bg-violet-50 p-2 text-xs ring-1 ring-violet-200">
          <div className="font-medium text-violet-800 mb-1">交叉分析</div>
          {out.analysis_notes.map((n, i) => (
            <div key={i} className="text-violet-700">
              {n.topic ? <strong>{n.topic}：</strong> : null}
              {n.note}
            </div>
          ))}
        </div>
      ) : null}

      {/* 需求列表 */}
      <div className="space-y-2">
        <div className="text-xs font-medium text-muted-foreground">生成的需求条目</div>
        {requirements.map((r: any, i: number) => (
          <div key={i} className="rounded border bg-card p-2">
            <div className="flex items-center gap-2">
              {r._confidence === "high" ? (
                <span title="多模型一致通过" className="text-xs text-emerald-500">✦</span>
              ) : r._confidence === "medium" ? (
                <span title="部分模型通过" className="text-xs text-amber-500">◇</span>
              ) : null}
              <span className="font-medium text-sm">{r.title}</span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                P{r.priority ?? 2}
              </span>
            </div>
            {r.description ? (
              <p className="mt-1 text-xs text-muted-foreground">{r.description}</p>
            ) : null}
            {r.tags?.length ? (
              <div className="mt-1 flex gap-1">
                {r.tags.map((t: string) => (
                  <span key={t} className="rounded bg-secondary px-1 py-0.5 text-[10px]">
                    {t}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
