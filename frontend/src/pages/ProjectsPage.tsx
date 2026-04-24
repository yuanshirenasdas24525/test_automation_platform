import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, projectsApi, runsApi } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Project, ProjectCategory, ProjectCreate } from "@/types/domain";

/**
 * 项目管理页。布局：顶部类型 Tab（api / web / app）→ 下面一张卡片网格 + 右上角「新建项目」。
 * 每张卡片右上角有一个三点菜单：编辑 / 删除。
 *
 * 后端有两个坑位需要注意：
 *  - `name` 长度 <= 10
 *  - `description` 长度 <= 50，且后端直接 `len(description)`，必须传字符串（不能 None）
 * 所以我们在 zod schema 里把 description 默认成空字符串。
 */

const PROJECT_TYPES: { value: ProjectCategory; label: string }[] = [
  { value: "api", label: "API" },
  { value: "web", label: "Web" },
  { value: "app", label: "App" },
];

/**
 * 后端历史数据里 `projects.type` 混写着 "API" / "api" / "Web" / "Mobile" 几种值，
 * 前端的下拉框只认 "api" / "web" / "app"。所以在 form 回填之前统一 normalize 一次，
 * 既避免 zod enum 校验失败，也避免 Select 拿不到匹配项而显示占位符（编辑项目时看到的"类型没回填"就是这个原因）。
 */
function normalizeProjectCategory(raw: string | null | undefined): ProjectCategory {
  if (!raw) return "api";
  const v = String(raw).trim().toLowerCase();
  if (v === "api") return "api";
  if (v === "web") return "web";
  // 旧数据里 app 项目被写作 "Mobile"，这里统一映射到 "app"
  if (v === "app" || v === "mobile" || v === "android" || v === "ios") return "app";
  return "api";
}

const projectSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "请输入项目名")
    .max(10, "名称最多 10 个字符"),
  type: z.enum(["api", "web", "app"]),
  description: z.string().max(50, "描述最多 50 个字符"),
});

type ProjectFormValues = z.infer<typeof projectSchema>;

export function ProjectsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawType = searchParams.get("type") ?? "api";
  const activeType: ProjectCategory =
    rawType === "web" || rawType === "app" ? rawType : "api";

  const navigate = useNavigate();

  const [editing, setEditing] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Project | null>(null);
  const [runningId, setRunningId] = useState<number | null>(null);

  const queryClient = useQueryClient();

  const projectsQuery = useQuery({
    queryKey: queryKeys.projects(activeType),
    queryFn: () => projectsApi.list(activeType),
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
    onSuccess: () => {
      toast.success("项目已删除");
      invalidate();
      setPendingDelete(null);
    },
    onError: handleError,
  });

  const runMutation = useMutation({
    mutationFn: (project: Project) =>
      runsApi.trigger({
        project: project.id,
        category: (project.type as ProjectCategory) ?? activeType,
        // 显式使用 v2 loader：它会带上 steps / environment 字段，
        // web / app 用例必需；api 用例走 v2 也能兼容（CaseExecutor 会自动合成 http_request step）
        v2: true,
      }),
    onMutate: (project) => setRunningId(project.id),
    onSettled: () => setRunningId(null),
    onSuccess: (res) => {
      toast.success(
        `已在后台启动 · ${res.case_number ?? 0} 条用例 · task ${
          res.task_id ?? "-"
        }`,
      );
      invalidate();
    },
    onError: handleError,
  });

  const handleTabChange = (value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set("type", value);
    setSearchParams(next, { replace: true });
  };

  const projects = projectsQuery.data ?? [];

  return (
    <div className="space-y-6 p-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">项目管理</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            创建、编辑和删除自动化测试项目。按类型切换列表。
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" />
          新建项目
        </Button>
      </div>

      <Tabs value={activeType} onValueChange={handleTabChange}>
        <TabsList>
          {PROJECT_TYPES.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

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
        <EmptyState
          type={activeType}
          onCreate={() => setCreating(true)}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {projects.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              onOpen={() => navigate(`/projects/${p.id}`)}
              onEdit={() => setEditing(p)}
              onDelete={() => setPendingDelete(p)}
              onRun={() => runMutation.mutate(p)}
              running={runningId === p.id}
            />
          ))}
        </div>
      )}

      <ProjectFormDialog
        open={creating}
        onOpenChange={(v) => !v && setCreating(false)}
        title="新建项目"
        defaultValues={{ name: "", type: activeType, description: "" }}
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
                type: normalizeProjectCategory(editing.type),
                description:
                  editing.description ?? editing.desc ?? "",
              }
            : { name: "", type: activeType, description: "" }
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// 卡片 + 状态小组件
// ---------------------------------------------------------------------------
function ProjectCard({
  project,
  onOpen,
  onEdit,
  onDelete,
  onRun,
  running,
}: {
  project: Project;
  onOpen: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onRun: () => void;
  running: boolean;
}) {
  const desc = project.description ?? project.desc ?? "";
  const lastStatus = project.last_status ?? "unknown";

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
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            title="运行全部用例"
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
      <CardContent className="flex-1 space-y-1 text-xs">
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

  const typeValue = watch("type");

  const typeOptions = useMemo(() => PROJECT_TYPES, []);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            项目是用例的最大容器。名称最多 10 字，描述最多 50 字。
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
              placeholder="比如：支付中心"
              {...register("name")}
            />
            {errors.name ? (
              <p className="text-xs text-destructive">{errors.name.message}</p>
            ) : null}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="project-type">类型</Label>
            <Select
              value={typeValue}
              onValueChange={(v) =>
                setValue("type", v as ProjectCategory, { shouldDirty: true })
              }
            >
              <SelectTrigger id="project-type">
                <SelectValue placeholder="选择类型" />
              </SelectTrigger>
              <SelectContent>
                {typeOptions.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
// 空态 / 骨架 / 错误
// ---------------------------------------------------------------------------
function EmptyState({
  type,
  onCreate,
}: {
  type: ProjectCategory;
  onCreate: () => void;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <div className="text-sm text-muted-foreground">
          当前没有 {type.toUpperCase()} 类型的项目。
        </div>
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
