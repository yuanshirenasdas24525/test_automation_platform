/** 版本详情页：4区文档管理 + 结构化版本信息 + 提测记录 + 关联模块。 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft, BookOpen, Bug, Eye, FileText, GanttChart, Globe, LayoutDashboard,
  Link2, Loader2, MoreHorizontal, Palette, Pencil, Plus, Sparkles, TestTube,
  Trash2, Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { ApiError, type ModulePickerNode, modulesApi, tasksApi, versionsApi } from "@/lib/api";
import type { DocItem, VersionEntry, VersionStatus } from "@/types/domain";
import { CreateBugModal } from "@/pages/tasks/CreateBugModal";

const STATUS_META: Record<VersionStatus, { label: string; tone: string }> = {
  planning: { label: "规划中", tone: "text-blue-700 bg-blue-50" },
  developing: { label: "开发中", tone: "text-amber-700 bg-amber-50" },
  testing: { label: "测试中", tone: "text-violet-700 bg-violet-50" },
  ready_to_release: { label: "待发版", tone: "text-cyan-700 bg-cyan-50" },
  released: { label: "已发布", tone: "text-emerald-700 bg-emerald-50" },
  archived: { label: "已归档", tone: "text-slate-600 bg-slate-100" },
};

const DOC_SECTIONS = [
  { key: "test_plan_items" as const, label: "测试计划", icon: TestTube, color: "text-blue-600" },
  { key: "requirement_doc_items" as const, label: "需求文档", icon: BookOpen, color: "text-violet-600" },
  { key: "design_doc_items" as const, label: "设计稿", icon: Palette, color: "text-pink-600" },
  { key: "ui_prototype_items" as const, label: "UI 原型图", icon: Globe, color: "text-emerald-600" },
];

const NOTE_FIELDS = [
  { key: "sql" as const, label: "SQL 变更", hint: "本次版本需要执行的 SQL 语句" },
  { key: "config" as const, label: "配置变更", hint: "需要调整或新增的配置项" },
  { key: "commands" as const, label: "常用命令", hint: "部署、回滚、数据修复等命令" },
  { key: "notes" as const, label: "注意事项与测试要点", hint: "该版本的风险点、测试重点、特殊说明" },
];

export function ProjectVersionDetailPage() {
  const { id: projectIdStr, vid: versionIdStr } = useParams<{ id: string; vid: string }>();
  const projectId = Number(projectIdStr);
  const versionId = Number(versionIdStr);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const versionQuery = useQuery({
    queryKey: ["version", projectId, versionId],
    queryFn: () => versionsApi.get(projectId, versionId),
    enabled: Number.isFinite(projectId) && Number.isFinite(versionId),
  });
  const modulesQuery = useQuery({
    queryKey: ["modules", projectId],
    queryFn: () => modulesApi.listForPicker(projectId),
    enabled: Number.isFinite(projectId),
  });

  const [fields, setFields] = useState({ sql: "", config: "", commands: "", notes: "" });
  const [docDialog, setDocDialog] = useState<{ section: string; label: string; item?: DocItem } | null>(null);
  const allModules = modulesQuery.data ?? [];
  const version = versionQuery.data;

  const [bugModalOpen, setBugModalOpen] = useState(false);
  const bugsQuery = useQuery({
    queryKey: ["tasks", "version-bugs", versionId],
    queryFn: () => tasksApi.list({ type: "bug", version_id: versionId }),
    enabled: Number.isFinite(versionId),
  });
  const bugs = bugsQuery.data ?? [];

  useEffect(() => {
    if (version) {
      try {
        const parsed = JSON.parse(version.release_notes || "{}");
        setFields({ sql: parsed.sql || "", config: parsed.config || "", commands: parsed.commands || "", notes: parsed.notes || "" });
      } catch {
        setFields({ sql: version.release_notes || "", config: "", commands: "", notes: "" });
      }
    }
  }, [version?.id]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["version", projectId, versionId] });

  const updateVersion = useMutation({
    mutationFn: (payload: Record<string, unknown>) => versionsApi.update(projectId, versionId, payload as any),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error((e as ApiError).message),
  });

  const saveField = (key: string, val: string) => {
    const updated = { ...fields, [key]: val };
    setFields(updated);
    updateVersion.mutate({ release_notes: JSON.stringify(updated) });
  };

  const addVersionEntry = (field: "frontend_versions" | "backend_versions") => {
    updateVersion.mutate({ [field]: [...(version?.[field] ?? []), { version: "", date: new Date().toISOString().slice(0, 10), notes: "" }] });
  };
  const updateVersionEntry = (field: "frontend_versions" | "backend_versions", idx: number, patch: Partial<VersionEntry>) => {
    const existing = [...(version?.[field] ?? [])];
    existing[idx] = { ...existing[idx], ...patch };
    updateVersion.mutate({ [field]: existing });
  };
  const removeVersionEntry = (field: "frontend_versions" | "backend_versions", idx: number) => {
    updateVersion.mutate({ [field]: (version?.[field] ?? []).filter((_, i) => i !== idx) });
  };

  const saveDocItem = (section: string, item: DocItem) => {
    const existing = ((version as any)?.[section] as DocItem[]) ?? [];
    const idx = existing.findIndex((x) => x.id === item.id);
    updateVersion.mutate({ [section]: idx >= 0 ? existing.map((x, i) => i === idx ? item : x) : [...existing, item] });
  };
  const deleteDocItem = (section: string, itemId: string) => {
    const existing = ((version as any)?.[section] as DocItem[]) ?? [];
    updateVersion.mutate({ [section]: existing.filter((x) => x.id !== itemId) });
  };

  if (versionQuery.isLoading) return <div className="p-6"><Skeleton className="h-96 w-full" /></div>;
  if (!version) return <div className="p-6 text-sm text-destructive">版本不存在</div>;

  const meta = STATUS_META[version.status];
  const associatedModules: ModulePickerNode[] = [];
  for (const mid of (version.associated_module_ids ?? [])) {
    const m = allModules.find((x) => x.id === mid);
    if (m) associatedModules.push(m);
  }

  return (
    <div className="p-6 space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 min-w-0">
          <Button variant="ghost" size="icon" onClick={() => navigate(`/projects/${projectId}?stack=management`)}><ArrowLeft className="h-4 w-4" /></Button>
          <GanttChart className="h-5 w-5 shrink-0" />
          <span className="font-medium">{version.version_name}</span>
          {version.display_name ? <span className="text-muted-foreground text-sm">— {version.display_name}</span> : null}
          <span className={cn("rounded px-1.5 py-0.5 text-xs", meta.tone)}>{meta.label}</span>
          <Button variant="outline" size="sm" onClick={() => navigate(`/projects/${projectId}/versions/${versionId}/board`)}>
            <LayoutDashboard className="mr-1 h-3.5 w-3.5" />
            看板视图
          </Button>
        </div>
        <Select value={version.status} onValueChange={(v) => updateVersion.mutate({ status: v })}>
          <SelectTrigger className="h-8 w-28"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="planning">规划中</SelectItem>
            <SelectItem value="developing">开发中</SelectItem>
            <SelectItem value="testing">测试中</SelectItem>
            <SelectItem value="released">已发布</SelectItem>
            <SelectItem value="archived">已归档</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 左侧：文档 + 提测记录 + 模块 */}
        <div className="space-y-4">
          {DOC_SECTIONS.map(({ key, label, icon: Icon, color }) => {
            const items = ((version as any)[key] as DocItem[]) ?? [];
            return (
              <Card key={key}>
                <CardContent className="py-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className={cn("text-xs font-medium flex items-center gap-1", color)}><Icon className="h-3.5 w-3.5" />{label}</span>
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setDocDialog({ section: key, label })}><Plus className="h-3.5 w-3.5" /></Button>
                  </div>
                  {items.length === 0 ? <p className="text-xs text-muted-foreground/50">暂无内容</p> : items.map((item) => (
                    <div key={item.id} className="flex items-center gap-1 group">
                      {item.type === "link" ? <Link2 className="h-3 w-3 shrink-0 text-blue-500" /> : item.type === "file" ? <FileText className="h-3 w-3 shrink-0 text-amber-500" /> : <Eye className="h-3 w-3 shrink-0 text-green-500" />}
                      {item.type === "link" ? (
                        <a href={item.url ?? "#"} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 hover:underline truncate flex-1 min-w-0">{item.name}</a>
                      ) : (
                        <button className="text-xs text-left truncate flex-1 min-w-0 hover:text-foreground" onClick={() => setDocDialog({ section: key, label, item })}>{item.name}</button>
                      )}
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100"><MoreHorizontal className="h-3.5 w-3.5" /></Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setDocDialog({ section: key, label, item })}>
                            <Pencil className="h-4 w-4 mr-1" />编辑
                          </DropdownMenuItem>
                          {key === "requirement_doc_items" && (
                            <DropdownMenuItem onClick={() => navigate(`/projects/${projectId}/requirements`)}>
                              <Sparkles className="h-4 w-4 mr-1" />AI 需求分析
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem className="text-destructive" onClick={() => { if (confirm(`删除${label}"${item.name}"？`)) deleteDocItem(key, item.id); }}>
                            <Trash2 className="h-4 w-4 mr-1" />删除
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  ))}
                </CardContent>
              </Card>
            );
          })}

          {(["frontend_versions", "backend_versions"] as const).map((field) => {
            const entries = (version?.[field] ?? []) as VersionEntry[];
            const label = field === "frontend_versions" ? "前端提测版本" : "后端服务版本";
            return (
              <Card key={field}>
                <CardContent className="py-3 space-y-2">
                  <div className="flex items-center justify-between"><span className="text-xs font-medium text-muted-foreground">{label}</span><Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => addVersionEntry(field)}><Plus className="h-3 w-3" /></Button></div>
                  {entries.map((entry, i) => (
                    <div key={i} className="flex items-center gap-1 text-xs">
                      <Input className="h-7 w-24 text-xs" value={entry.version} onChange={(e) => updateVersionEntry(field, i, { version: e.target.value })} placeholder="版本号" />
                      <Input className="h-7 w-20 text-xs" type="date" value={entry.date} onChange={(e) => updateVersionEntry(field, i, { date: e.target.value })} />
                      <Button variant="ghost" size="icon" className="h-6 w-6 text-destructive" onClick={() => removeVersionEntry(field, i)}><Trash2 className="h-3 w-3" /></Button>
                    </div>
                  ))}
                </CardContent>
              </Card>
            );
          })}

          <Card>
            <CardContent className="py-3 space-y-2">
              <span className="text-xs font-medium text-muted-foreground">关联模块</span>
              {associatedModules.length === 0 ? <p className="text-xs text-muted-foreground/50">暂未关联</p> : (
                <div className="flex flex-wrap gap-1">{associatedModules.map((m) => <span key={m.id} className="rounded bg-secondary px-1.5 py-0.5 text-[10px]">{m.name}</span>)}</div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* 右侧：结构化版本信息 */}
        <div className="lg:col-span-2 space-y-4">
          {NOTE_FIELDS.map(({ key, label, hint }) => (
            <Card key={key}>
              <CardContent className="py-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div><span className="text-sm font-medium">{label}</span><span className="text-xs text-muted-foreground ml-2">{hint}</span></div>
                  <Button size="sm" variant="ghost" onClick={() => saveField(key, fields[key])} disabled={updateVersion.isPending}>
                    {updateVersion.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : null}保存
                  </Button>
                </div>
                <Textarea className="min-h-[120px] font-mono text-sm" value={fields[key]}
                  onChange={(e) => setFields((p) => ({ ...p, [key]: e.target.value }))}
                  onBlur={(e) => { if (e.target.value !== fields[key]) saveField(key, e.target.value); }}
                  placeholder={`输入${label}...`} />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Bug 列表 */}
      <Card>
        <CardContent className="py-3 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Bug className="h-4 w-4 text-red-500" />
              <span className="text-sm font-medium">Bug</span>
              <span className="text-xs text-muted-foreground">({bugs.length})</span>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => navigate(`/projects/${projectId}/versions/${versionId}/board?tab=bugs`)}>
                查看全部
              </Button>
              <Button size="sm" onClick={() => setBugModalOpen(true)}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                创建 Bug
              </Button>
            </div>
          </div>
          {bugsQuery.isLoading ? (
            <p className="text-xs text-muted-foreground">加载中…</p>
          ) : bugs.length === 0 ? (
            <p className="text-xs text-muted-foreground">该版本暂无 Bug</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b text-left text-xs text-muted-foreground">
                <tr>
                  <th className="py-2 pr-2">标题</th>
                  <th className="py-2 pr-2">严重度</th>
                  <th className="py-2 pr-2">状态</th>
                  <th className="py-2 pr-2">需求</th>
                  <th className="py-2 pr-2">负责人</th>
                </tr>
              </thead>
              <tbody>
                {bugs.map((b) => (
                  <tr
                    key={b.id}
                    className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                    onClick={() => navigate(`/tasks/${b.id}`)}
                  >
                    <td className="py-2 pr-2">{b.title}</td>
                    <td className={`py-2 pr-2 text-xs font-semibold ${severityClass(b.severity)}`}>
                      {b.severity ?? "—"}
                    </td>
                    <td className="py-2 pr-2">
                      <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${statusBadge(b.status).className}`}>
                        {statusBadge(b.status).label}
                      </span>
                    </td>
                    <td className="py-2 pr-2 text-muted-foreground text-xs">
                      #{b.requirement_id}
                    </td>
                    <td className="py-2 pr-2 text-muted-foreground text-xs">
                      {b.assignee_dev_id ? `#${b.assignee_dev_id}` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <CreateBugModal
        open={bugModalOpen}
        onOpenChange={setBugModalOpen}
        versionId={versionId}
      />

      <DocEditDialog state={docDialog} onClose={() => setDocDialog(null)}
        onSave={(section, item) => { saveDocItem(section, item); setDocDialog(null); }}
        onDelete={(section, itemId) => { deleteDocItem(section, itemId); setDocDialog(null); }} />
    </div>
  );
}

function severityClass(s: string | null | undefined): string {
  switch (s) {
    case "P0": return "text-red-600";
    case "P1": return "text-orange-500";
    case "P2": return "text-amber-600";
    case "P3": return "text-muted-foreground";
    default: return "";
  }
}

function statusBadge(status: string): { label: string; className: string } {
  switch (status) {
    case "pending": return { label: "待处理", className: "bg-gray-100 text-gray-600" };
    case "dev_doing": return { label: "修复中", className: "bg-blue-100 text-blue-700" };
    case "dev_done": return { label: "已修复", className: "bg-emerald-100 text-emerald-700" };
    case "test_doing": return { label: "测试中", className: "bg-violet-100 text-violet-700" };
    case "passed": return { label: "通过", className: "bg-green-100 text-green-700" };
    case "failed": return { label: "失败", className: "bg-red-100 text-red-700" };
    case "closed": return { label: "关闭", className: "bg-gray-100 text-gray-500" };
    default: return { label: status, className: "" };
  }
}

function DocEditDialog({ state, onClose, onSave, onDelete }: {
  state: { section: string; label: string; item?: DocItem } | null;
  onClose: () => void; onSave: (section: string, item: DocItem) => void; onDelete: (section: string, itemId: string) => void;
}) {
  const isEdit = !!state?.item;
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"link" | "text" | "file">("link");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");

  useEffect(() => {
    if (state) { setName(state.item?.name || ""); setMode(state.item?.type || "link"); setUrl(state.item?.url || ""); setContent(state.item?.content || ""); }
  }, [state]);

  if (!state) return null;

  return (
    <Dialog open={!!state} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader><DialogTitle>{isEdit ? `编辑 ${state.label}` : `添加 ${state.label}`}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1"><Label className="text-xs">名称 *</Label><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="如：Sprint 12 测试计划" autoFocus /></div>
          <div className="flex gap-2">
            <Button variant={mode === "link" ? "default" : "outline"} size="sm" onClick={() => setMode("link")}><Link2 className="h-3 w-3 mr-1" />链接</Button>
            <Button variant={mode === "text" ? "default" : "outline"} size="sm" onClick={() => setMode("text")}><Eye className="h-3 w-3 mr-1" />文本</Button>
            <Button variant={mode === "file" ? "default" : "outline"} size="sm" onClick={() => setMode("file")}><Upload className="h-3 w-3 mr-1" />文件</Button>
          </div>
          {mode === "text" ? (
            <Textarea rows={8} value={content} onChange={(e) => setContent(e.target.value)} placeholder="粘贴文档内容..." />
          ) : (
            <div className="space-y-1"><Label className="text-xs">{mode === "link" ? "URL" : "文件路径"}</Label><Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={mode === "link" ? "https://..." : "/path/to/file"} /></div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>取消</Button>
          {isEdit && state.item && <Button variant="destructive" size="sm" onClick={() => { const it = state.item!; if (confirm(`删除"${it.name}"？`)) onDelete(state.section, it.id); }}>删除</Button>}
          <Button onClick={() => { if (name.trim()) onSave(state.section, { id: state.item?.id || crypto.randomUUID(), name: name.trim(), type: mode, url: mode === "text" ? null : (url || null), content: mode === "text" ? content : null }); }} disabled={!name.trim()}>{isEdit ? "保存" : "添加"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
