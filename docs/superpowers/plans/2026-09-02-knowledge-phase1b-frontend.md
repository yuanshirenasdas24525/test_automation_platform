# 知识库改造 · 阶段 1b（分类与检索 · 前端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端接上阶段 1a 的目录/标签/搜索后端：知识库 tab 的左侧共享栏切换成「知识库目录树」，面板换成搜索框 + 标签筛选 + 卡片列表 + 置顶。

**Architecture:** 新增自包含组件 `KnowledgeFolderTree`（自己拉目录、增删改移），在 `ProjectManagementPage` 的共享左栏里按 `activeTab==='knowledge'` 条件渲染它（替代模块树）。`KnowledgeBasePanel` 从「按模块过滤 + 表格」改成「按目录/搜索/标签过滤 + 卡片列表 + 置顶」。`KnowledgeDocDialog` 加目录选择器 + 标签选择器。API/类型层先补齐。视觉参照已批准的原型。

**Tech Stack:** React 19 + TS strict + Tailwind + shadcn/ui（Radix）+ @tanstack/react-query + sonner。别名 `@/* → frontend/src/*`。

**前置约定（务必先读）：**
- 前端命令在 `frontend/` 下跑：`npm run typecheck`（tsc -b --noEmit）、`npm run lint`（eslint，`--max-warnings 0` 很严）、`npm run build`（tsc -b && vite build）。**每个任务结束都要 typecheck 过**；改动完前端最终要 build（否则后端 54351 看的是旧 dist）。
- API 走 `src/lib/api.ts` 的 `request<T>()`（自动拆 `ApiEnvelope<T>`，业务错误抛 `ApiError`）。数据获取用 react-query；用户提示用 `sonner` 的 `toast`。
- 领域类型集中在 `src/types/domain.ts`。
- 有 `venv` / `node_modules` 软链在目录里，**永远别 `git add -A`**，只 add 指定文件。
- 不写传统单测（前端无测试框架）；验证 = typecheck + lint + build + 浏览器渲染核对。

**本阶段不做：** 阅读视图/TOC（阶段 2）、文件上传预览（阶段 3）、导入导出（阶段 4）。目录/标签的拖拽排序本阶段用「上移/下移 + 菜单移动」够用，不做拖拽（YAGNI，可后续加）。

**已知约束（来自 1a 评审，前端必须遵守）：** 文档 PUT 是「全量替换」语义——编辑保存时 **必须回传 `folder_id`**（根级传 `null`），否则文档会被移回根。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `frontend/src/types/domain.ts` | 加 `KnowledgeFolderNode` / `KnowledgeTag` 类型；`KnowledgeDoc`/`KnowledgeDocCreate`/`KnowledgeDocUpdate` 补字段 | 修改 |
| `frontend/src/lib/api.ts` | 加 `knowledgeFoldersApi` / `knowledgeTagsApi`；`knowledgeApi.list` 扩展 opts；加 `knowledgeApi.pin` | 修改 |
| `frontend/src/pages/knowledge/KnowledgeFolderTree.tsx` | 目录树（自包含：查询 + 选择 + 增删改移） | 新建 |
| `frontend/src/pages/ProjectManagementPage.tsx` | 知识库 tab 时左栏渲染目录树；`selectedFolderId` 状态；传给面板 | 修改 |
| `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx` | 搜索框 + 标签筛选 + 卡片列表 + 置顶（替代模块过滤/表格） | 重写 |
| `frontend/src/pages/knowledge/KnowledgeDocDialog.tsx` | 加目录选择器 + 标签多选（含新建标签） | 修改 |

---

## Task 1: API + 类型层

**Files:**
- Modify: `frontend/src/types/domain.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: 类型（`domain.ts`）**

在 `KnowledgeDoc` 接口（约 2010 行）里，给它补三个可选字段（在 `include_in_rag` 附近）：
```typescript
  folder_id?: number | null;
  is_pinned?: boolean;
  tags?: KnowledgeTagLite[];
```
在 `KnowledgeDoc` 接口**之前**新增这些类型：
```typescript
export interface KnowledgeTagLite {
  id: number;
  name: string;
  color?: string | null;
}

export interface KnowledgeTag {
  id: number;
  project_id: number;
  name: string;
  color?: string | null;
}

export interface KnowledgeFolderNode {
  id: number;
  project_id: number;
  parent_id: number | null;
  name: string;
  sort_order: number;
  children: KnowledgeFolderNode[];
}
```
找到 `KnowledgeDocCreate` 与 `KnowledgeDocUpdate` 接口，各补两个可选字段：
```typescript
  folder_id?: number | null;
  tag_ids?: number[];
```
（若这两个接口不在 domain.ts 而在 api.ts，就地在其定义处补。先 grep `KnowledgeDocCreate` 定位。）

- [ ] **Step 2: API（`api.ts`）**

在 `knowledgeApi` 定义处，把 `list` 的 opts 扩展，并新增 `pin`：
```typescript
export const knowledgeApi = {
  list(
    projectId: number,
    opts: { module_id?: number | null; folder_id?: number | null; tag_id?: number | null; q?: string } = {},
  ) {
    const qs = new URLSearchParams({ project_id: String(projectId) });
    if (opts.module_id != null) qs.set("module_id", String(opts.module_id));
    if (opts.folder_id != null) qs.set("folder_id", String(opts.folder_id));
    if (opts.tag_id != null) qs.set("tag_id", String(opts.tag_id));
    if (opts.q && opts.q.trim()) qs.set("q", opts.q.trim());
    return request<KnowledgeDoc[]>(`/api/knowledge?${qs.toString()}`);
  },
  get(id: number) {
    return request<KnowledgeDoc>(`/api/knowledge/${id}`);
  },
  create(body: KnowledgeDocCreate) {
    return request<KnowledgeDoc>("/api/knowledge", { method: "POST", body });
  },
  update(id: number, body: KnowledgeDocUpdate) {
    return request<KnowledgeDoc>(`/api/knowledge/${id}`, { method: "PUT", body });
  },
  pin(id: number, pinned: boolean) {
    return request<KnowledgeDoc>(`/api/knowledge/${id}/pin`, { method: "PATCH", body: { pinned } });
  },
  remove(id: number) {
    return request<{ id: number }>(`/api/knowledge/${id}`, { method: "DELETE" });
  },
};
```
在 `knowledgeApi` **之后**新增两个 API 对象：
```typescript
export const knowledgeFoldersApi = {
  list(projectId: number) {
    return request<KnowledgeFolderNode[]>(`/api/knowledge/folders?project_id=${projectId}`);
  },
  create(body: { project_id: number; name: string; parent_id?: number | null }) {
    return request<KnowledgeFolderNode>("/api/knowledge/folders", { method: "POST", body });
  },
  update(id: number, body: { name?: string; parent_id?: number | null; move_to_root?: boolean }) {
    return request<KnowledgeFolderNode>(`/api/knowledge/folders/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<{ id: number }>(`/api/knowledge/folders/${id}`, { method: "DELETE" });
  },
};

export const knowledgeTagsApi = {
  list(projectId: number) {
    return request<KnowledgeTag[]>(`/api/knowledge/tags?project_id=${projectId}`);
  },
  create(body: { project_id: number; name: string; color?: string | null }) {
    return request<KnowledgeTag>("/api/knowledge/tags", { method: "POST", body });
  },
  update(id: number, body: { name?: string; color?: string | null }) {
    return request<KnowledgeTag>(`/api/knowledge/tags/${id}`, { method: "PUT", body });
  },
  remove(id: number) {
    return request<{ id: number }>(`/api/knowledge/tags/${id}`, { method: "DELETE" });
  },
};
```
确保 `KnowledgeFolderNode` / `KnowledgeTag` 从 `@/types/domain` 正确 import（api.ts 顶部已从 domain 批量 import，把这两个名字加进去）。

- [ ] **Step 3: typecheck**

Run: `cd frontend && npm run typecheck`
Expected: 无输出（通过）。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): API + 类型层——目录/标签/搜索/置顶

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: KnowledgeFolderTree 组件（自包含）

**Files:**
- Create: `frontend/src/pages/knowledge/KnowledgeFolderTree.tsx`

- [ ] **Step 1: 写组件**

创建 `frontend/src/pages/knowledge/KnowledgeFolderTree.tsx`。要求：
- Props：`{ projectId: number; selectedFolderId: number | null; onSelect: (id: number | null) => void }`。
- 自己用 react-query 拉 `knowledgeFoldersApi.list(projectId)`（queryKey `["knowledge-folders", projectId]`）。
- 顶部标题「知识库目录」+ 一个 `[+]` 按钮（在根级新建目录，用 `prompt()` 取名，调 `knowledgeFoldersApi.create`）。
- 一个「全部文档」根项（`onSelect(null)`，selectedFolderId===null 高亮）。
- 递归渲染 `children`：每项可点选（`onSelect(id)`，高亮 selectedFolderId===id），有展开/收起箭头（有 children 时），hover 出一个 shadcn `DropdownMenu`：新建子目录 / 重命名（都用 `prompt()`）/ 删除（`confirm()`，提示「删除目录，其中文档与子目录会上移到父级」）。
- 所有写操作成功后 `queryClient.invalidateQueries({ queryKey: ["knowledge-folders", projectId] })` 并 `toast.success`；失败 `toast.error((e as ApiError).message)`。删除当前选中目录后 `onSelect(null)`。
- 视觉：跟 `ProjectManagementPage` 里模块树的 class 风格一致（`text-sm`、hover:bg-muted、选中 `bg-primary/10 text-primary`、缩进 `style={{ marginLeft: depth*16 }}`、箭头用 lucide `ChevronRight/ChevronDown`、目录图标 lucide `Folder/FolderOpen`）。用 `@/components/ui/button`、`@/components/ui/dropdown-menu`、`@/components/ui/skeleton`。

完整代码：
```tsx
/** 知识库独立目录树（左栏，替代模块树）。自包含：查询 + 选择 + 增删改移。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ApiError, knowledgeFoldersApi } from "@/lib/api";
import type { KnowledgeFolderNode } from "@/types/domain";

export function KnowledgeFolderTree({
  projectId,
  selectedFolderId,
  onSelect,
}: {
  projectId: number;
  selectedFolderId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const foldersQuery = useQuery({
    queryKey: ["knowledge-folders", projectId],
    queryFn: () => knowledgeFoldersApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["knowledge-folders", projectId] });

  const createFolder = useMutation({
    mutationFn: (v: { name: string; parent_id: number | null }) =>
      knowledgeFoldersApi.create({ project_id: projectId, name: v.name, parent_id: v.parent_id }),
    onSuccess: () => { toast.success("已创建目录"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const renameFolder = useMutation({
    mutationFn: (v: { id: number; name: string }) => knowledgeFoldersApi.update(v.id, { name: v.name }),
    onSuccess: () => { toast.success("已重命名"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const removeFolder = useMutation({
    mutationFn: (id: number) => knowledgeFoldersApi.remove(id),
    onSuccess: (_d, id) => { toast.success("已删除目录"); if (id === selectedFolderId) onSelect(null); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });

  const toggle = (id: number) =>
    setExpanded((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const onCreate = (parentId: number | null) => {
    const name = window.prompt(parentId == null ? "新建根目录名称" : "新建子目录名称");
    if (name && name.trim()) createFolder.mutate({ name: name.trim(), parent_id: parentId });
  };
  const onRename = (f: KnowledgeFolderNode) => {
    const name = window.prompt("重命名目录", f.name);
    if (name && name.trim()) renameFolder.mutate({ id: f.id, name: name.trim() });
  };
  const onDelete = (f: KnowledgeFolderNode) => {
    if (window.confirm(`删除目录「${f.name}」？其中的文档与子目录会上移到父级，不会被删除。`))
      removeFolder.mutate(f.id);
  };

  const renderNode = (f: KnowledgeFolderNode, depth: number) => {
    const hasChildren = f.children.length > 0;
    const isOpen = expanded.has(f.id);
    const isSel = selectedFolderId === f.id;
    return (
      <div key={f.id}>
        <div
          className={cn(
            "group flex items-center gap-1 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted transition-colors",
            isSel && "bg-primary/10 text-primary font-medium",
          )}
          style={{ marginLeft: depth * 16 }}
          onClick={() => { if (hasChildren) toggle(f.id); onSelect(f.id); }}
        >
          {hasChildren
            ? isOpen ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />
            : <span className="w-3 shrink-0" />}
          {isSel || isOpen ? <FolderOpen className="h-4 w-4 text-blue-500 shrink-0" /> : <Folder className="h-4 w-4 text-amber-500 shrink-0" />}
          <span className="flex-1 truncate">{f.name}</span>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover:opacity-100"
                onClick={(e) => e.stopPropagation()}>
                <MoreHorizontal className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onCreate(f.id); }}>
                <FolderPlus className="h-4 w-4 mr-1" />新建子目录
              </DropdownMenuItem>
              <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onRename(f); }}>
                <Pencil className="h-4 w-4 mr-1" />重命名
              </DropdownMenuItem>
              <DropdownMenuItem className="text-destructive" onClick={(e) => { e.stopPropagation(); onDelete(f); }}>
                <Trash2 className="h-4 w-4 mr-1" />删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {hasChildren && isOpen && <div>{f.children.map((c) => renderNode(c, depth + 1))}</div>}
      </div>
    );
  };

  const roots = foldersQuery.data ?? [];

  return (
    <div className="w-64 shrink-0 border-r bg-muted/20 p-3 overflow-y-auto">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-muted-foreground">知识库目录</span>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => onCreate(null)}>
          <Plus className="h-3 w-3" />
        </Button>
      </div>
      <div
        className={cn(
          "flex items-center gap-1 rounded px-2 py-1.5 text-sm cursor-pointer hover:bg-muted mb-0.5",
          selectedFolderId === null && "bg-primary/10 text-primary font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        <span className="w-3 shrink-0" />
        <Folder className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="flex-1 truncate">全部文档</span>
      </div>
      {foldersQuery.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : roots.length === 0 ? (
        <p className="text-xs text-muted-foreground py-4">暂无目录，点右上角 [+] 新建</p>
      ) : (
        <div className="space-y-0.5">{roots.map((f) => renderNode(f, 0))}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: typecheck**

Run: `cd frontend && npm run typecheck`
Expected: 通过。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/knowledge/KnowledgeFolderTree.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 知识库目录树组件（自包含增删改）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ProjectManagementPage —— 知识库 tab 左栏切目录树

**Files:**
- Modify: `frontend/src/pages/ProjectManagementPage.tsx`

- [ ] **Step 1: import + state**

在文件顶部 import 区加：
```typescript
import { KnowledgeFolderTree } from "./knowledge/KnowledgeFolderTree";
```
在 `const [selectedModuleId, setSelectedModuleId] = useState<number | null>(null);` 下面加：
```typescript
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(null);
```

- [ ] **Step 2: 左栏条件渲染目录树**

找到渲染共享左栏的地方 `{moduleTree}`（约第 392 行，在 `<div className="flex flex-1 min-h-0 gap-0">` 之后）。替换为：
```tsx
        {activeTab === "knowledge" ? (
          <KnowledgeFolderTree
            projectId={projectId}
            selectedFolderId={selectedFolderId}
            onSelect={setSelectedFolderId}
          />
        ) : (
          moduleTree
        )}
```

- [ ] **Step 3: 传 selectedFolderId 给面板**

找到 `<KnowledgeBasePanel ... />`（约第 485 行）。把它的 props 改为（去掉对 module 的依赖，加 folder）：
```tsx
              <KnowledgeBasePanel
                projectId={projectId}
                selectedFolderId={selectedFolderId}
                modules={modules}
                moduleNames={moduleNames}
              />
```
（保留 `modules` / `moduleNames`：文档 dialog 里仍可能展示模块名；`selectedModuleId` 不再传给知识库面板。）

- [ ] **Step 4: typecheck**

> 注：此时 `KnowledgeBasePanel` 的 props 还是旧的（Task 4 才改），typecheck 可能报 `selectedFolderId` 不在 Panel props 上。**允许本步 typecheck 失败**，Task 4 改完 Panel 后整体通过。为让本任务可独立提交，先只确认本文件语法无误：
Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json 2>&1 | grep -i "ProjectManagementPage" | head`
Expected: 只可能出现与 `KnowledgeBasePanel` props 相关的报错（Task 4 修复），不应有其他 ProjectManagementPage 自身语法错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ProjectManagementPage.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 知识库 tab 左栏切换为目录树 + selectedFolderId

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: KnowledgeBasePanel 重写（搜索 + 标签筛选 + 卡片 + 置顶）

**Files:**
- Modify（重写）: `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx`

- [ ] **Step 1: 重写面板**

用以下内容**整体替换** `KnowledgeBasePanel.tsx`。要点：props 改为 `{ projectId, selectedFolderId, modules, moduleNames }`；顶部工具条含搜索框 + 标签筛选下拉 + 新建按钮；主体是卡片网格（参照原型）；每卡有分类芯片 / `已入库`or`仅人读` / 标签芯片 / 作者时间 / 置顶星标；点卡片开只读抽屉，hover 出编辑/删除/置顶。搜索用 300ms 防抖后进 query key。

```tsx
/** 知识库面板：目录/搜索/标签过滤 + 卡片列表 + 置顶。左侧目录树在父页。 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, Pin, PinOff, Pencil, Plus, Search, Sparkles, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, knowledgeApi, knowledgeTagsApi, type ModulePickerNode } from "@/lib/api";
import { stripHtml } from "@/lib/utils";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeDoc } from "@/types/domain";
import { KnowledgeDocDialog } from "./KnowledgeDocDialog";
import { KnowledgeDocViewDrawer } from "./KnowledgeDocViewDrawer";

const TYPE_LABELS = new Map<string, string>(KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]));
const ALL_TAGS = "__all__";

export function KnowledgeBasePanel({
  projectId,
  selectedFolderId,
  modules,
  moduleNames,
}: {
  projectId: number;
  selectedFolderId: number | null;
  modules: ModulePickerNode[];
  moduleNames: Map<number, string>;
}) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [rawSearch, setRawSearch] = useState("");
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState<string>(ALL_TAGS);

  // 搜索防抖 300ms
  useEffect(() => {
    const t = setTimeout(() => setSearch(rawSearch), 300);
    return () => clearTimeout(t);
  }, [rawSearch]);

  const tagsQuery = useQuery({
    queryKey: ["knowledge-tags", projectId],
    queryFn: () => knowledgeTagsApi.list(projectId),
    enabled: Number.isFinite(projectId),
  });

  const docsQuery = useQuery({
    queryKey: ["knowledge", projectId, selectedFolderId, search, tagFilter],
    queryFn: () =>
      knowledgeApi.list(projectId, {
        folder_id: selectedFolderId ?? undefined,
        q: search || undefined,
        tag_id: tagFilter === ALL_TAGS ? undefined : Number(tagFilter),
      }),
    enabled: Number.isFinite(projectId),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["knowledge", projectId] });

  const remove = useMutation({
    mutationFn: (id: number) => knowledgeApi.remove(id),
    onSuccess: () => { toast.success("已删除"); invalidate(); },
    onError: (e) => toast.error((e as ApiError).message),
  });
  const pin = useMutation({
    mutationFn: (v: { id: number; pinned: boolean }) => knowledgeApi.pin(v.id, v.pinned),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error((e as ApiError).message),
  });

  const docs = docsQuery.data ?? [];
  const tags = tagsQuery.data ?? [];

  const openCreate = () => { setEditingId(null); setDialogOpen(true); };
  const openEdit = (id: number) => { setViewingId(null); setEditingId(id); setDialogOpen(true); };

  const typeColor = useMemo(() => (t: string) => {
    switch (t) {
      case "api_contract": return "var(--primary, #2563eb)";
      case "business_rule": return "#b45309";
      case "data_model": return "#7c3aed";
      default: return "#64748b";
    }
  }, []);

  return (
    <div>
      {/* 工具条 */}
      <div className="mb-3 flex items-center gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-8 h-9" placeholder="搜索标题、正文…" value={rawSearch}
            onChange={(e) => setRawSearch(e.target.value)} />
        </div>
        <Select value={tagFilter} onValueChange={setTagFilter}>
          <SelectTrigger className="h-9 w-36"><SelectValue placeholder="全部标签" /></SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_TAGS}>全部标签</SelectItem>
            {tags.map((t) => <SelectItem key={t.id} value={String(t.id)}>{t.name}</SelectItem>)}
          </SelectContent>
        </Select>
        <span className="flex-1" />
        <Button size="sm" onClick={openCreate}><Plus className="h-4 w-4 mr-1" />新建文档</Button>
      </div>

      {docsQuery.isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(268px,1fr))] gap-3">
          <Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" />
        </div>
      ) : docs.length === 0 ? (
        <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
          <BookOpen className="mx-auto mb-2 h-6 w-6 opacity-40" />
          {search || tagFilter !== ALL_TAGS ? "没有符合条件的文档" : selectedFolderId != null ? "该目录下暂无文档" : "暂无知识文档，点右上角「新建文档」开始沉淀"}
        </CardContent></Card>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(268px,1fr))] gap-3">
          {docs.map((d) => (
            <article key={d.id}
              className="group relative overflow-hidden rounded-xl border bg-card p-4 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition cursor-pointer"
              onClick={() => setViewingId(d.id)}>
              <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: typeColor(d.context_type) }} />
              <div className="flex items-start gap-1">
                <h3 className="flex-1 font-semibold text-sm leading-snug line-clamp-2">{d.title}</h3>
                <div className="flex items-center opacity-0 group-hover:opacity-100" onClick={(e) => e.stopPropagation()}>
                  <Button variant="ghost" size="icon" className="h-7 w-7"
                    onClick={() => pin.mutate({ id: d.id, pinned: !d.is_pinned })} title={d.is_pinned ? "取消置顶" : "置顶"}>
                    {d.is_pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEdit(d.id)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                    onClick={() => { if (confirm(`删除知识文档「${d.title}」？`)) remove.mutate(d.id); }}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {d.is_pinned && <Pin className="h-3.5 w-3.5 text-amber-500 shrink-0 group-hover:hidden" />}
              </div>
              {stripHtml(d.summary ?? "") ? (
                <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{stripHtml(d.summary ?? "")}</p>
              ) : null}
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                  {TYPE_LABELS.get(d.context_type) ?? d.context_type}
                </span>
                {d.include_in_rag ? (
                  <span className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                    <Sparkles className="h-3 w-3" />已入库
                  </span>
                ) : (
                  <span className="rounded bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">仅人读</span>
                )}
                {(d.tags ?? []).map((t) => (
                  <span key={t.id} className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground">
                    {t.name}
                  </span>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-2 border-t pt-2 text-[11px] text-muted-foreground tabular-nums">
                {d.updated_at ? d.updated_at.slice(0, 16).replace("T", " ") : "—"}
              </div>
            </article>
          ))}
        </div>
      )}

      <KnowledgeDocViewDrawer
        open={viewingId != null}
        docId={viewingId}
        moduleNames={moduleNames}
        onClose={() => setViewingId(null)}
        onEdit={(id) => openEdit(id)}
      />

      <KnowledgeDocDialog
        open={dialogOpen}
        projectId={projectId}
        modules={modules}
        editingId={editingId}
        defaultFolderId={selectedFolderId}
        onClose={() => setDialogOpen(false)}
        onSaved={() => setDialogOpen(false)}
      />
    </div>
  );
}
```

- [ ] **Step 2: typecheck**

> `KnowledgeDocDialog` 的 `defaultFolderId` prop 在 Task 5 才加，本步 typecheck 可能就此报错，Task 5 修复。先确认本文件其他部分无误：
Run: `cd frontend && npx tsc --noEmit -p tsconfig.app.json 2>&1 | grep -i "KnowledgeBasePanel" | head`
Expected: 至多出现与 `defaultFolderId`/`KnowledgeDocDialog` 相关的报错（Task 5 修），无其他。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/knowledge/KnowledgeBasePanel.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 面板重写——搜索+标签筛选+卡片列表+置顶

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: KnowledgeDocDialog —— 目录 + 标签选择器

**Files:**
- Modify: `frontend/src/pages/knowledge/KnowledgeDocDialog.tsx`

当前 dialog（阶段 0）已有：标题、所属模块 Select、分类 Select、正文富文本、纳入 RAG 勾选。本任务：把「所属模块」换成「所属目录」；加一行「标签」多选（现有标签芯片可切换 + 一个输入框新建标签）；prop 从 `defaultModuleId` 改为 `defaultFolderId`；保存时带 `folder_id` 与 `tag_ids`（**编辑时也要回传 folder_id，根级传 null**）。

- [ ] **Step 1: 读现有文件，按下述改造**

改动点（基于阶段 0 的 `KnowledgeDocDialog.tsx`）：

(a) props：把 `defaultModuleId: number | null` 改为 `defaultFolderId: number | null`。

(b) import 补：
```typescript
import { useQuery } from "@tanstack/react-query"; // 已有则不重复
import { knowledgeTagsApi } from "@/lib/api";
```
并从 `@/lib/api` 里确保 `knowledgeFoldersApi` 也被 import。

(c) 状态：把 `moduleId` 相关状态替换为目录与标签：
```typescript
  const [folderId, setFolderId] = useState<string>(NO_FOLDER);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [newTag, setNewTag] = useState("");
```
其中顶部常量：`const NO_FOLDER = "__root__";`（沿用已有 `DEFAULT_TYPE`）。删掉 `NO_MODULE` 相关。

(d) 拉目录树（拍平成下拉选项）与标签：
```typescript
  const foldersQuery = useQuery({
    queryKey: ["knowledge-folders", projectId],
    queryFn: () => knowledgeFoldersApi.list(projectId),
    enabled: open && Number.isFinite(projectId),
  });
  const tagsQuery = useQuery({
    queryKey: ["knowledge-tags", projectId],
    queryFn: () => knowledgeTagsApi.list(projectId),
    enabled: open && Number.isFinite(projectId),
  });
  // 目录树拍平成「缩进名称」选项
  const flatFolders = useMemo(() => {
    const out: { id: number; label: string }[] = [];
    const walk = (nodes: any[], depth: number) => {
      for (const n of nodes) {
        out.push({ id: n.id, label: `${"　".repeat(depth)}${n.name}` });
        if (n.children?.length) walk(n.children, depth + 1);
      }
    };
    walk(foldersQuery.data ?? [], 0);
    return out;
  }, [foldersQuery.data]);
```
（`useMemo` 从 react 引入。）

(e) 表单重置逻辑（`useEffect`）：编辑时 `setFolderId(d.folder_id != null ? String(d.folder_id) : NO_FOLDER)`、`setTagIds((d.tags ?? []).map(t => t.id))`；新建时 `setFolderId(defaultFolderId != null ? String(defaultFolderId) : NO_FOLDER)`、`setTagIds([])`。detailQuery 的 `KnowledgeDoc` 详情里已含 `folder_id`/`tags`（阶段 1a serialize 已补）。

(f) 保存 body：
```typescript
      const body = {
        title: title.trim(),
        content_html: contentHtml,
        folder_id: folderId === NO_FOLDER ? null : Number(folderId),
        context_type: contextType,
        include_in_rag: includeInRag,
        tag_ids: tagIds,
      };
```
（移除 `module_id`。）

(g) UI：把「所属模块」那个 Select 换成「所属目录」：
```tsx
              <div className="space-y-1.5">
                <Label>所属目录</Label>
                <Select value={folderId} onValueChange={setFolderId}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NO_FOLDER}>（根级 / 不指定）</SelectItem>
                    {flatFolders.map((f) => (
                      <SelectItem key={f.id} value={String(f.id)}>{f.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
```
并在「正文」上方或「纳入 RAG」下方加「标签」区：
```tsx
            <div className="space-y-1.5">
              <Label>标签</Label>
              <div className="flex flex-wrap items-center gap-1.5">
                {(tagsQuery.data ?? []).map((t) => {
                  const on = tagIds.includes(t.id);
                  return (
                    <button type="button" key={t.id}
                      className={`rounded-full border px-2.5 py-0.5 text-xs transition ${on ? "border-transparent bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"}`}
                      onClick={() => setTagIds((prev) => on ? prev.filter((x) => x !== t.id) : [...prev, t.id])}>
                      {t.name}
                    </button>
                  );
                })}
                <input
                  className="h-7 w-28 rounded border px-2 text-xs outline-none focus:border-primary"
                  placeholder="+ 新标签回车"
                  value={newTag}
                  onChange={(e) => setNewTag(e.target.value)}
                  onKeyDown={async (e) => {
                    if (e.key === "Enter" && newTag.trim()) {
                      e.preventDefault();
                      try {
                        const tag = await knowledgeTagsApi.create({ project_id: projectId, name: newTag.trim() });
                        setNewTag("");
                        await queryClient.invalidateQueries({ queryKey: ["knowledge-tags", projectId] });
                        setTagIds((prev) => prev.includes(tag.id) ? prev : [...prev, tag.id]);
                      } catch (err) {
                        toast.error((err as ApiError).message);
                      }
                    }
                  }}
                />
              </div>
            </div>
```
（`queryClient` 从 `useQueryClient()` 取；`ApiError`/`toast` 已 import；`useMemo` 引入。）

- [ ] **Step 2: typecheck 全绿（此时 Task 3/4 的悬挂类型应一并解决）**

Run: `cd frontend && npm run typecheck`
Expected: 通过（无输出）。若报错，逐个修到通过。

- [ ] **Step 3: lint**

Run: `cd frontend && npm run lint`
Expected: 0 warning 0 error（`--max-warnings 0`）。常见需修：未用变量（如残留的 `selectedModuleId`）、`any`（给 `walk` 的 nodes 用 `KnowledgeFolderNode[]` 替代 `any[]`）。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/pages/knowledge/KnowledgeDocDialog.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 文档弹窗——目录选择器 + 标签多选/新建

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 构建 + 浏览器渲染核对

**Files:** 无（验证）

- [ ] **Step 1: typecheck + lint + build**

Run:
```bash
cd frontend && npm run typecheck && npm run lint && npm run build
```
Expected: 三者全过；`vite build` 产出 `frontend/dist`。

- [ ] **Step 2: 浏览器渲染核对（用 Browser 工具）**

启动后端（EAGER 即可）与前端 dev，或直接用已 build 的 dist 经后端 54351 访问。核对项：
1. 进入某项目 → 知识库 tab：左栏显示「知识库目录」树（不是模块树），能新建/重命名/删除目录、点选目录过滤。
2. 顶部搜索框输入关键字 → 卡片列表按标题/正文过滤；标签下拉筛选生效。
3. 卡片：分类色条、`已入库`/`仅人读`、标签芯片、更新时间；hover 出置顶/编辑/删除；点击开只读抽屉。
4. 新建/编辑弹窗：能选所属目录、能勾选/新建标签；保存后卡片反映目录与标签。
5. 切到「需求池」tab：左栏恢复为模块树（未被破坏）。

- [ ] **Step 3: 截图存档**（可选）
用浏览器工具截「知识库 tab」一张，`SendUserFile` 发给用户确认视觉。

---

## 收尾与验收

- [ ] 知识库 tab 左栏 = 独立目录树；其它 tab 左栏 = 模块树（互不影响）。
- [ ] 搜索 / 标签筛选 / 目录过滤 / 置顶四者可用；卡片列表视觉贴原型。
- [ ] 弹窗可设目录 + 标签；编辑保存回传 folder_id（不会把已分目录的文档移回根）。
- [ ] `npm run typecheck && npm run lint && npm run build` 全绿。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 1「前端左树右列 + 全文搜索框 + 标签筛选」。目录树=Task 2/3；搜索+标签筛选+卡片=Task 4；目录/标签录入=Task 5。选定布局方案 A（知识库 tab 切换共享左栏）。
- **占位扫描**：新文件（api/types/folder tree/panel）给了完整代码；dialog 因是既有文件的多点改造，给了每处的完整替换片段与精确位置，无「TODO/略」。
- **类型一致**：`KnowledgeFolderNode`/`KnowledgeTag`/`KnowledgeTagLite` 在 domain 定义，api/组件一致引用；`knowledgeApi.list` opts、`knowledgeApi.pin`、`knowledgeFoldersApi`/`knowledgeTagsApi` 方法签名在 Task 1 定义、Task 2/4/5 一致调用；Panel↔Dialog 的 `defaultFolderId`、Page↔Panel 的 `selectedFolderId` 对齐。
- **1a 评审约束落实**：Task 5 (f) 保存 body 恒带 `folder_id`（根级 null），满足「PUT 全量替换、必须回传 folder_id」的约束。
- **兼容性**：仅知识库 tab 改用目录；其它 tab 的模块树逻辑与 `selectedModuleId` 不动。
