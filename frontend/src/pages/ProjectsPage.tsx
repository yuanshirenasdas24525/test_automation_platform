import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, projectsApi, runsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import {
  ALL_PROJECT_STACKS,
  type Project,
  type ProjectCreate,
  type ProjectStack,
} from "@/types/domain";
import { DevicePickerDialog } from "@/components/device-picker-dialog";

/**
 * 项目管理页（v2 / 多栈项目模型）。
 *
 * 关键变化（vs v1）：
 *  - 项目不再绑死单一栈：每个项目通过 `enabled_stacks` 启用多个栈（API/Web/App/Functional）。
 *  - 顶层不再有"按类型切 Tab"——所有项目同列展示，卡片上用 chip 显示启用的栈。
 *  - chip / Tab 都按固定顺序显示：功能 → API → Web → App（user 决策）。
 *  - 新建 / 编辑表单的"类型"字段从单选 Select 换成多选 Checkbox，至少选 1 个。
 *  - "运行"按钮逻辑：
 *      0 个自动化栈（只勾了 functional） → 不显示"运行"按钮（功能用例靠人工勾，进入详情页操作）；
 *      1 个自动化栈                         → 直接 trigger（app 仍走设备选择器）；
 *      2+ 个自动化栈                        → 弹一个小 picker 让用户挑要跑哪个栈。
 *
 * 后端约束（与 v1 相同）：
 *  - `name` 长度 <= 10
 *  - `description` 长度 <= 50（后端会 len() 直接比较，必须传字符串而非 None）
 */

/** 中文名映射，仅用于展示。chip / 选项一律按 ALL_PROJECT_STACKS 顺序渲染。 */
const STACK_LABELS: Record<ProjectStack, string> = {
  functional: "功能",
  api: "API",
  web: "Web",
  android: "Android",
  ios: "iOS",
};

/**
 * 自动化执行链路支持的栈集合（functional 不参与）。
 * 注意不要直接 import `AUTOMATED_CASE_TYPES` —— 那是 case_type 集合（含 mixed），
 * 这里要的是 ProjectStack 集合。
 */
const AUTOMATED_STACKS: ProjectStack[] = ["api", "web", "android", "ios"];

/** 哪些栈走 Appium 设备 → 触发设备选择器。 */
const APP_LIKE_STACKS: ProjectStack[] = ["android", "ios"];

const projectSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "请输入项目名")
    .max(10, "名称最多 10 个字符"),
  /** 至少启用一个栈；前端 zod + 后端 pydantic 双重保证。 */
  enabled_stacks: z
    .array(z.enum(["api", "web", "android", "ios", "functional"]))
    .min(1, "至少启用一个栈"),
  description: z.string().max(50, "描述最多 50 个字符"),
});

type ProjectFormValues = z.infer<typeof projectSchema>;

export function ProjectsPage() {
  const navigate = useNavigate();

  const [editing, setEditing] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  const [runningId, setRunningId] = useState<number | null>(null);
  /** App 项目运行前的设备选择器：暂存待运行 (project, stack)，确认后再发请求。 */
  const [pendingAppRun, setPendingAppRun] = useState<
    { project: Project; stack: ProjectStack } | null
  >(null);
  /** 多栈项目"挑栈"小 picker：暂存待运行项目，user 选完栈后再走 handleRunStack。 */
  const [pendingStackPick, setPendingStackPick] = useState<Project | null>(null);

  const queryClient = useQueryClient();

  // v2 起列表不再按 stack 过滤 —— 顶层不分 Tab，全部项目一起展示。
  // 如果未来要回到"分 Tab"，只需把 stack 传给 list() + queryKey 即可，后端已经支持 ?stack=。
  const projectsQuery = useQuery({
    queryKey: queryKeys.projects(),
    queryFn: () => projectsApi.list(),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["projects"] });

  const handleError = (err: unknown) => {
    const msg =
      err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败";
    toast.error(msg);
  };

  const createMutation = useMutation({
    mutationFn: (body: ProjectCreate) => projectsApi.create(body),
    onSuccess: () => {
      toast.success("项目已创建");
      invalidate();
      setCreating(false);
    },
    onError: handleError,
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: ProjectCreate }) =>
      projectsApi.update(id, body),
    onSuccess: () => {
      toast.success("项目已更新");
      invalidate();
      setEditing(null);
    },
    onError: handleError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => projectsApi.remove(id),
    onMutate: async (id: number) => {
      await queryClient.cancelQueries({ queryKey: ["projects"] });
      const snapshots = queryClient.getQueriesData<Project[]>({ queryKey: ["projects"] });
      for (const [key, data] of snapshots) {
        if (Array.isArray(data)) {
          queryClient.setQueryData(
            key,
            data.filter((project) => project.id !== id),
          );
        }
      }
      setPendingDelete(null);
      return { snapshots };
    },
    onError: (err, _id, ctx) => {
      ctx?.snapshots?.forEach(([key, data]) => queryClient.setQueryData(key, data));
      handleError(err);
    },
    onSuccess: () => {
      toast.success("项目已删除");
    },
    onSettled: () => invalidate(),
  });

  const runMutation = useMutation({
    mutationFn: ({
      project,
      stack,
      deviceId,
    }: {
      project: Project;
      stack: ProjectStack;
      /** 仅 app 栈使用：null 表示走自动池分配，数字则锁定指定设备。 */
      deviceId?: number | null;
    }) =>
      runsApi.trigger({
        project: project.id,
        // RunTestRequest.category 只接受自动化栈，functional 不会到这里
        category: stack as Exclude<ProjectStack, "functional">,
        device_id: deviceId,
      }),
    onMutate: ({ project }) => setRunningId(project.id),
    onSettled: () => setRunningId(null),
    onSuccess: (res) => {
      toast.success(
        `已在后台启动 · ${res.case_number ?? 0} 条用例 · task ${
          res.task_id ?? "-"
        }`,
      );
      invalidate();
      setPendingAppRun(null);
      setPendingStackPick(null);
    },
    onError: handleError,
  });

  /**
   * 计算项目可触发的"自动化栈"列表（按固定顺序，不含 functional）。
   * 卡片右上角"运行"按钮根据这个数组决定行为。
   */
  const automatedStacksOf = (project: Project): ProjectStack[] => {
    const set = new Set(project.enabled_stacks ?? []);
    return AUTOMATED_STACKS.filter((s) => set.has(s));
  };

  /**
   * 卡片"运行"入口：
   *  - 0 个自动化栈：理论上按钮已被隐藏（automatedStacksOf 长度为 0），
   *    防御性兜底直接 toast 警告；
   *  - 1 个自动化栈：直接走 runStack（app 栈仍要走设备选择器）；
   *  - 2+ 个自动化栈：弹小 picker 让用户挑栈。
   */
  const handleRunProject = (project: Project) => {
    const stacks = automatedStacksOf(project);
    if (stacks.length === 0) {
      toast.info("当前项目没有可自动化的栈，进入项目详情手动操作功能用例");
      return;
    }
    if (stacks.length === 1) {
      runStack(project, stacks[0]);
      return;
    }
    setPendingStackPick(project);
  };

  /** 触发某个项目的某个栈：app/android/ios 都走设备选择器；其它直接 trigger。 */
  const runStack = (project: Project, stack: ProjectStack) => {
    if (APP_LIKE_STACKS.includes(stack)) {
      setPendingAppRun({ project, stack });
      return;
    }
    runMutation.mutate({ project, stack });
  };

  const projects = projectsQuery.data ?? [];

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">项目管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            创建、编辑和删除自动化测试项目。每个项目可同时启用多个栈（API / Web / App / 功能）。
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" />
          新建项目
        </Button>
      </div>

      {projectsQuery.isLoading ? (
        <ProjectGridSkeleton />
      ) : projectsQuery.isError ? (
        <ErrorBox
          message={
            projectsQuery.error instanceof Error
              ? projectsQuery.error.message
              : "加载失败"
          }
          onRetry={() => projectsQuery.refetch()}
        />
      ) : projects.length === 0 ? (
        <EmptyState onCreate={() => setCreating(true)} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              automatedStacks={automatedStacksOf(p)}
              onOpen={() => navigate(`/projects/${p.id}?stack=management`)}
              onEdit={() => setEditing(p)}
              onDelete={() => setPendingDelete(p)}
              onRun={() => handleRunProject(p)}
              running={runningId === p.id}
            />
          ))}
        </div>
      )}

      <ProjectFormDialog
        open={creating}
        onOpenChange={(v) => !v && setCreating(false)}
        title="新建项目"
        defaultValues={{
          name: "",
          enabled_stacks: ["api"],
          description: "",
        }}
        submitting={createMutation.isPending}
        onSubmit={(values) => createMutation.mutate(values)}
      />

      <ProjectFormDialog
        open={editing !== null}
        onOpenChange={(v) => !v && setEditing(null)}
        title="编辑项目"
        defaultValues={
          editing
            ? {
                name: editing.name,
                enabled_stacks: normalizeStacks(editing.enabled_stacks),
                description: editing.description ?? editing.desc ?? "",
              }
            : { name: "", enabled_stacks: ["api"], description: "" }
        }
        submitting={updateMutation.isPending}
        onSubmit={(values) =>
          editing && updateMutation.mutate({ id: editing.id, body: values })
        }
      />

      <DeleteConfirmDialog
        project={pendingDelete}
        onCancel={() => setPendingDelete(null)}
        onConfirm={(id) => deleteMutation.mutate(id)}
        submitting={deleteMutation.isPending}
      />

      {/* App 栈专属：运行前选设备。其他栈走 runStack 直接 trigger，这个弹窗 open 永远 false。 */}
      <DevicePickerDialog
        open={pendingAppRun !== null}
        onCancel={() => setPendingAppRun(null)}
        onConfirm={(deviceId) => {
          if (!pendingAppRun) return;
          runMutation.mutate({
            project: pendingAppRun.project,
            stack: pendingAppRun.stack,
            deviceId,
          });
        }}
        submitting={runMutation.isPending}
        target={
          pendingAppRun
            ? `项目 ${pendingAppRun.project.name} · ${STACK_LABELS[pendingAppRun.stack]}`
            : undefined
        }
      />

      {/* 多栈项目专属：挑选要跑哪个栈。 */}
      <RunStackPickerDialog
        project={pendingStackPick}
        stacks={
          pendingStackPick ? automatedStacksOf(pendingStackPick) : []
        }
        onCancel={() => setPendingStackPick(null)}
        onPick={(stack) => {
          if (!pendingStackPick) return;
          // 关掉 picker，下游 runStack 会按需打开 device picker。
          const project = pendingStackPick;
          setPendingStackPick(null);
          runStack(project, stack);
        }}
      />
    </div>
  );
}

/**
 * 后端兜底：保证 enabled_stacks 是 ProjectStack[] 子集 + 至少 1 个。
 * 极端历史数据（迁移漏掉？空字符串？）走默认 ["api"]，避免编辑表单崩溃。
 */
function normalizeStacks(raw: unknown): ProjectStack[] {
  if (!Array.isArray(raw)) return ["api"];
  const set = new Set<string>(raw.filter((v) => typeof v === "string"));
  const filtered = ALL_PROJECT_STACKS.filter((s) => set.has(s));
  return filtered.length > 0 ? filtered : ["api"];
}

// ---------------------------------------------------------------------------
// 卡片 + 状态小组件
// ---------------------------------------------------------------------------
function ProjectCard({
  project,
  automatedStacks,
  onOpen,
  onEdit,
  onDelete,
  onRun,
  running,
}: {
  project: Project;
  /** 已经按 ALL_PROJECT_STACKS 顺序过滤 + functional 排除好的"可自动化栈"列表。 */
  automatedStacks: ProjectStack[];
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onRun: () => void;
  running: boolean;
}) {
  const desc = project.description ?? project.desc ?? "";
  const lastStatus = project.last_status ?? "unknown";
  const stacks = useMemo(
    () => normalizeStacks(project.enabled_stacks),
    [project.enabled_stacks],
  );

  // 整张卡片可点 → 进详情。
  // 内部 Run 按钮 / DropdownMenu 都要 stopPropagation，避免误触发 onOpen。
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="flex cursor-pointer flex-col transition-shadow hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 pb-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate text-base">{project.name}</CardTitle>
          <p className="mt-1 line-clamp-2 min-h-[2.5rem] text-xs text-muted-foreground">
            {desc || "（暂无描述）"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1" onClick={stop}>
          {/* 只有当存在自动化栈时才显示运行按钮；纯 functional 项目隐藏。 */}
          {automatedStacks.length > 0 ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              title={
                automatedStacks.length === 1
                  ? `运行 ${STACK_LABELS[automatedStacks[0]]} 全部用例`
                  : "选择栈并运行"
              }
              disabled={running}
              onClick={(e) => {
                stop(e);
                onRun();
              }}
            >
              {running ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              <span className="sr-only">运行</span>
            </Button>
          ) : null}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={stop}
              >
                <MoreHorizontal className="h-4 w-4" />
                <span className="sr-only">更多操作</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={stop}>
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  onEdit();
                }}
              >
                <Pencil className="h-4 w-4" />
                编辑
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={(e) => {
                  e.preventDefault();
                  onDelete();
                }}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="h-4 w-4" />
                删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </CardHeader>
      <CardContent className="flex-1 space-y-2 text-xs">
        <StackChips stacks={stacks} />
        <Stat label="用例数" value={String(project.case_count ?? 0)} />
        <Stat
          label="通过率"
          value={
            project.pass_rate !== undefined && project.pass_rate !== null
              ? `${project.pass_rate}%`
              : "--"
          }
        />
      </CardContent>
      <CardFooter className="justify-between border-t pt-3 text-xs text-muted-foreground">
        <StatusBadge status={lastStatus} />
        <span>{project.last_run_time || "从未执行"}</span>
      </CardFooter>
    </Card>
  );
}

/**
 * 卡片上的栈 chip。
 * 排序按 ALL_PROJECT_STACKS（功能 → API → Web → App，user 决策的固定顺序）。
 * 不显示后端入库顺序，避免老数据里 type 字段历史污染影响展示。
 */
function StackChips({ stacks }: { stacks: ProjectStack[] }) {
  if (stacks.length === 0) {
    return <span className="text-muted-foreground">未启用栈</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {stacks.map((s) => (
        <span
          key={s}
          className="inline-flex items-center rounded-full border bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
        >
          {STACK_LABELS[s]}
        </span>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s = status.toLowerCase();
  if (s === "success" || s === "pass" || s === "passed") {
    return (
      <span className="inline-flex items-center gap-1 text-green-600">
        <CheckCircle2 className="h-3.5 w-3.5" />
        上次通过
      </span>
    );
  }
  if (s === "fail" || s === "failed" || s === "error") {
    return (
      <span className="inline-flex items-center gap-1 text-destructive">
        <XCircle className="h-3.5 w-3.5" />
        上次失败
      </span>
    );
  }
  return <span>未执行</span>;
}

// ---------------------------------------------------------------------------
// 表单 Dialog（复用于新建 + 编辑）
// ---------------------------------------------------------------------------
function ProjectFormDialog({
  open,
  onOpenChange,
  title,
  defaultValues,
  submitting,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  title: string;
  defaultValues: ProjectFormValues;
  submitting: boolean;
  onSubmit: (values: ProjectFormValues) => void;
}) {
  // 每次打开重置表单
  const form = useForm<ProjectFormValues>({
    resolver: zodResolver(projectSchema),
    defaultValues,
    values: defaultValues,
  });

  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch,
  } = form;

  const stacksValue = watch("enabled_stacks");

  /** 切换某个栈的勾选；保证至少 1 个（zod 也会校验，这里防御）。 */
  const toggleStack = (stack: ProjectStack, checked: boolean) => {
    const set = new Set(stacksValue);
    if (checked) {
      set.add(stack);
    } else {
      set.delete(stack);
    }
    // 按 ALL_PROJECT_STACKS 顺序排序回写，便于后端 / 后续展示一致
    const next = ALL_PROJECT_STACKS.filter((s) => set.has(s));
    setValue("enabled_stacks", next, {
      shouldDirty: true,
      shouldValidate: true,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            项目是用例的最大容器；可同时启用多个栈。名称最多 10 字，描述最多 50 字。
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={handleSubmit((values) => onSubmit(values))}
        >
          <div className="space-y-1.5">
            <Label htmlFor="project-name">名称</Label>
            <Input
              id="project-name"
              maxLength={10}
              autoFocus
              placeholder="比如：电商平台"
              {...register("name")}
            />
            {errors.name ? (
              <p className="text-xs text-destructive">{errors.name.message}</p>
            ) : null}
          </div>

          <div className="space-y-2">
            <Label>启用栈（至少 1 个）</Label>
            {/*
              用 Button toggle 而不是 Checkbox：
                - 项目里还没引入 @radix-ui/react-checkbox，不想为这个表单单独加依赖；
                - toggle 的视觉重量更接近 chip，更容易让用户理解"这是个二选一开关"。
              variant 切换：选中态 default（实心），未选 outline。
            */}
            <div className="flex flex-wrap gap-2">
              {/* 顺序按 ALL_PROJECT_STACKS（保证视觉与卡片 chips 一致）。 */}
              {ALL_PROJECT_STACKS.map((s) => {
                const checked = stacksValue.includes(s);
                return (
                  <Button
                    key={s}
                    type="button"
                    size="sm"
                    variant={checked ? "default" : "outline"}
                    onClick={() => toggleStack(s, !checked)}
                    aria-pressed={checked}
                  >
                    {STACK_LABELS[s]}
                  </Button>
                );
              })}
            </div>
            {errors.enabled_stacks ? (
              <p className="text-xs text-destructive">
                {errors.enabled_stacks.message as string}
              </p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-desc">描述</Label>
            <Textarea
              id="project-desc"
              maxLength={50}
              rows={3}
              placeholder="一句话描述项目（可选）"
              {...register("description")}
            />
            {errors.description ? (
              <p className="text-xs text-destructive">
                {errors.description.message}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              确定
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 删除确认
// ---------------------------------------------------------------------------
function DeleteConfirmDialog({
  project,
  onCancel,
  onConfirm,
  submitting,
}: {
  project: Project | null;
  onCancel: () => void;
  onConfirm: (id: number) => void;
  submitting: boolean;
}) {
  return (
    <Dialog open={project !== null} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>删除项目</DialogTitle>
          <DialogDescription>
            确定要删除「{project?.name}」吗？项目下的模块和用例将一同被删除，且不可恢复。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={() => project && onConfirm(project.id)}
            disabled={submitting}
          >
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            删除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 多栈项目"挑栈跑"小 picker
// ---------------------------------------------------------------------------
function RunStackPickerDialog({
  project,
  stacks,
  onCancel,
  onPick,
}: {
  project: Project | null;
  stacks: ProjectStack[];
  onCancel: () => void;
  onPick: (stack: ProjectStack) => void;
}) {
  return (
    <Dialog open={project !== null} onOpenChange={(v) => !v && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>选择要运行的栈</DialogTitle>
          <DialogDescription>
            「{project?.name}」启用了多个自动化栈，请选择本次要执行的栈。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-1 gap-2">
          {stacks.map((s) => (
            <Button
              key={s}
              variant="outline"
              className="justify-start"
              onClick={() => onPick(s)}
            >
              <Play className="h-4 w-4" />
              运行 {STACK_LABELS[s]} 全部用例
            </Button>
          ))}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            取消
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// 空态 / 骨架 / 错误
// ---------------------------------------------------------------------------
function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <div className="text-sm text-muted-foreground">还没有任何项目。</div>
        <Button onClick={onCreate} variant="outline">
          <Plus className="h-4 w-4" />
          创建第一个项目
        </Button>
      </CardContent>
    </Card>
  );
}

function ProjectGridSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-24" />
            <Skeleton className="mt-2 h-3 w-40" />
          </CardHeader>
          <CardContent className="space-y-2">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-full" />
          </CardContent>
          <CardFooter>
            <Skeleton className="h-3 w-20" />
          </CardFooter>
        </Card>
      ))}
    </div>
  );
}

function ErrorBox({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Card className="border-destructive/50">
      <CardContent className="flex flex-col items-start gap-3 py-6">
        <div className="text-sm text-destructive">加载失败：{message}</div>
        <Button onClick={onRetry} variant="outline" size="sm">
          重试
        </Button>
      </CardContent>
    </Card>
  );
}
