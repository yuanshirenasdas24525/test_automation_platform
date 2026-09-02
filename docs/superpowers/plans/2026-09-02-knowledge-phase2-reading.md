# 知识库改造 · 阶段 2（阅读体验 + 版本历史）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强阅读抽屉（正文目录大纲 TOC + 锚点跳转 + 字号调节）+ 文档版本历史（每次编辑存快照，可查看/回滚）+ 删除确认改为正规弹窗。

**Architecture:** 沿用现有 720px 右侧 `SideDrawer`（不新开全屏）。后端：`update_doc` 在覆盖前写一版 `KnowledgeDocumentVersion` 快照（裁剪到最近 20 版），新增 restore；三个版本 REST 端点挂在 `/api/knowledge/{doc_id}/versions*`。前端：`KnowledgeDocViewDrawer` 重写为「阅读 / 历史」双模式——阅读模式渲染正文 + 右侧 TOC（正文渲染后用 DOM 查询 h1/h2/h3 生成，锚点滚动）+ 字号切换；历史模式列版本、预览、回滚。删除确认用新建的 `ConfirmDialog`（基于既有 Dialog）替代 `confirm()`。

**Tech Stack:** SQLAlchemy 2.0 + FastAPI + PostgreSQL；React 19 + TS strict + Tailwind + shadcn/ui + react-query + sonner。

**前置约定（务必先读）：**
- 包名 `server/`；Python 用 `./venv/bin/python`；DB 命令先 `set -a && . ./.env && set +a`。
- 前端命令在 `frontend/` 下；`npm run typecheck`/`npm run lint`（严，`--max-warnings 0`）/`npm run build`。**注意**：本仓 `npm run lint` 全仓有 3 条**既有** `react-hooks/exhaustive-deps` 警告（`FunctionalCasesPage.tsx`、`ui-recording/WebUiCaseGenerationDialog.tsx`），非本项目引入；验收时对**本阶段改动的文件**单独跑 `npx eslint <files> --max-warnings 0` 必须 0 warning，全仓 lint 的既有 3 条不算本阶段回归。
- 有 `venv`/`node_modules` 软链，**永远别 `git add -A`**，只 add 指定文件。
- 版本表 `knowledge_document_versions` 阶段 0 已建（字段 id/document_id/title/content_html/editor_id/created_at），删文档时 ORM `cascade="all, delete-orphan"` 已清版本，无需新迁移。

**本阶段不做：** 版本 diff 对比（只列表/查看/回滚）；全屏阅读页（保留抽屉）；文件上传（阶段 3）；导入导出（阶段 4）。模块/需求那些非知识库的 `confirm()` 不动。

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `server/services/knowledge_version_service.py` | 版本快照/裁剪/列表/取单版 + `content_changed` 纯函数 | 新建 |
| `server/services/knowledge_service.py` | `update_doc` 覆盖前写快照；新增 `restore_version` | 修改 |
| `server/api/knowledge.py` | 3 个版本端点（list/get/restore） | 修改 |
| `tests/knowledge/test_version.py` | `content_changed` 纯函数单测 | 新建 |
| `frontend/src/types/domain.ts` | `KnowledgeDocVersion` 类型 | 修改 |
| `frontend/src/lib/api.ts` | `knowledgeApi.versions/getVersion/restoreVersion` | 修改 |
| `frontend/src/components/ui/confirm-dialog.tsx` | 通用确认弹窗（基于 Dialog） | 新建 |
| `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx` | 删除用 ConfirmDialog | 修改 |
| `frontend/src/pages/knowledge/KnowledgeFolderTree.tsx` | 删除用 ConfirmDialog | 修改 |
| `frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx` | 重写：阅读(TOC+字号)/历史双模式 | 修改（重写） |

---

## Task 1: 版本纯函数单测（红）

**Files:** Create `tests/knowledge/test_version.py`

- [ ] **Step 1: 写失败测试**

```python
"""知识库版本 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_version_service as kvs


def test_content_changed_true_on_title_change():
    assert kvs.content_changed("旧标题", "<p>a</p>", "新标题", None) is True


def test_content_changed_true_on_html_change():
    assert kvs.content_changed("t", "<p>a</p>", None, "<p>b</p>") is True


def test_content_changed_false_when_same_or_none():
    assert kvs.content_changed("t", "<p>a</p>", None, None) is False
    assert kvs.content_changed("t", "<p>a</p>", "t", "<p>a</p>") is False


def test_content_changed_handles_none_old_html():
    assert kvs.content_changed("t", None, None, "") is False
    assert kvs.content_changed("t", None, None, "<p>x</p>") is True
```

- [ ] **Step 2: 跑确认失败**

Run: `./venv/bin/python -m pytest tests/knowledge/test_version.py -v`
Expected: FAIL —— `knowledge_version_service` 不存在。

- [ ] **Step 3: 提交**

```bash
git add tests/knowledge/test_version.py
git commit -m "$(cat <<'EOF'
test(knowledge): 版本 content_changed 单测（红）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 版本 service + 接入 update_doc + restore_version

**Files:**
- Create: `server/services/knowledge_version_service.py`
- Modify: `server/services/knowledge_service.py`

- [ ] **Step 1: 写版本 service**

创建 `server/services/knowledge_version_service.py`：

```python
"""知识库文档版本（KnowledgeDocumentVersion）服务层 —— 阶段 2。

每次编辑覆盖正文前存一版当前内容的快照；仅保留最近 MAX_VERSIONS 版。
"""
from __future__ import annotations

from typing import List, Optional

from database.models import KnowledgeDocumentVersion

MAX_VERSIONS = 20


def content_changed(
    old_title: Optional[str],
    old_html: Optional[str],
    new_title: Optional[str],
    new_html: Optional[str],
) -> bool:
    """标题或正文将发生实际变化则 True。new_* 为 None 表示该字段本次不改。"""
    if new_title is not None and new_title != (old_title or ""):
        return True
    if new_html is not None and new_html != (old_html or ""):
        return True
    return False


def snapshot(session, doc, *, editor_id: Optional[int] = None) -> KnowledgeDocumentVersion:
    """把 doc 的当前 title/content_html 存为一版，并裁剪到最近 MAX_VERSIONS 版。"""
    v = KnowledgeDocumentVersion(
        document_id=doc.id,
        title=doc.title,
        content_html=doc.content_html or "",
        editor_id=editor_id,
    )
    session.add(v)
    session.flush()
    _prune(session, doc.id)
    return v


def _prune(session, document_id: int) -> None:
    ids = [
        r[0]
        for r in session.query(KnowledgeDocumentVersion.id)
        .filter(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.id.desc())
        .all()
    ]
    stale = ids[MAX_VERSIONS:]
    if stale:
        session.query(KnowledgeDocumentVersion).filter(
            KnowledgeDocumentVersion.id.in_(stale)
        ).delete(synchronize_session=False)
        session.flush()


def list_versions(session, document_id: int) -> List[KnowledgeDocumentVersion]:
    return (
        session.query(KnowledgeDocumentVersion)
        .filter(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.id.desc())
        .all()
    )


def get_version(session, version_id: int) -> Optional[KnowledgeDocumentVersion]:
    return (
        session.query(KnowledgeDocumentVersion)
        .filter(KnowledgeDocumentVersion.id == version_id)
        .first()
    )


def serialize_version(v: KnowledgeDocumentVersion, *, detail: bool = False) -> dict:
    data = {
        "id": v.id,
        "document_id": v.document_id,
        "title": v.title,
        "editor_id": v.editor_id,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if detail:
        data["content_html"] = v.content_html or ""
    return data
```

- [ ] **Step 2: 接入 `update_doc` + 加 `restore_version`（`knowledge_service.py`）**

(a) 在 `update_doc` 函数体**最前面**（`if title is not None:` 之前）插入快照逻辑：
```python
    # 版本快照：标题/正文将改变时，先把当前内容存一版（阶段 2）
    if doc.id is not None:
        from server.services import knowledge_version_service as kvs
        new_title_norm = title.strip()[:255] if title is not None else None
        if kvs.content_changed(doc.title, doc.content_html, new_title_norm, content_html):
            kvs.snapshot(session, doc, editor_id=editor_id)
```

(b) 在 `update_doc` 函数**之后**新增 `restore_version`：
```python
def restore_version(session, doc, version, *, editor_id: Optional[int] = None):
    """把文档回滚到某个历史版本。回滚前先给当前内容存一版（便于再撤销）。"""
    from server.services import knowledge_version_service as kvs
    kvs.snapshot(session, doc, editor_id=editor_id)
    doc.title = (version.title or "")[:255]
    doc.content_html = version.content_html or ""
    doc.content = html_to_text(doc.content_html)
    if editor_id is not None:
        doc.editor_id = editor_id
    session.flush()
    sync_rag_projection(session, doc)
    return doc
```

- [ ] **Step 3: 单测转绿 + 编译**

Run:
```bash
./venv/bin/python -m pytest tests/knowledge/test_version.py -v && \
./venv/bin/python -m compileall server/services/knowledge_version_service.py server/services/knowledge_service.py
```
Expected: 4 passed；compile 无错。

- [ ] **Step 4: 提交**

```bash
git add server/services/knowledge_version_service.py server/services/knowledge_service.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 版本 service——编辑存快照(裁剪20版) + 回滚

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 版本 REST 端点

**Files:** Modify `server/api/knowledge.py`

- [ ] **Step 1: 加三个端点**

在 `server/api/knowledge.py` 里（放在 `delete_knowledge` 之前或 `pin_knowledge` 附近）新增：

```python
@router.get("/{doc_id}/versions")
def list_doc_versions(doc_id: int, db: DBDep):
    doc = _require_doc_in_project(db.session, doc_id)
    from server.services import knowledge_version_service as kvs
    return {
        "status": "success",
        "data": [kvs.serialize_version(v) for v in kvs.list_versions(db.session, doc.id)],
    }


@router.get("/{doc_id}/versions/{version_id}")
def get_doc_version(doc_id: int, version_id: int, db: DBDep):
    doc = _require_doc_in_project(db.session, doc_id)
    from server.services import knowledge_version_service as kvs
    v = kvs.get_version(db.session, version_id)
    if not v or v.document_id != doc.id:
        raise HTTPException(status_code=404, detail=f"版本不存在：{version_id}")
    return {"status": "success", "data": kvs.serialize_version(v, detail=True)}


@router.post("/{doc_id}/versions/{version_id}/restore")
def restore_doc_version(doc_id: int, version_id: int, db: DBDep, current_user: CurrentUserDep):
    doc = _require_doc_in_project(db.session, doc_id)
    from server.services import knowledge_version_service as kvs
    v = kvs.get_version(db.session, version_id)
    if not v or v.document_id != doc.id:
        raise HTTPException(status_code=404, detail=f"版本不存在：{version_id}")
    doc = knowledge_service.restore_version(db.session, doc, v, editor_id=current_user.id)
    return {"status": "success", "data": knowledge_service.serialize(doc, detail=True)}
```

- [ ] **Step 2: 编译 + 路由自查**

Run:
```bash
./venv/bin/python -m compileall server/api/knowledge.py && \
set -a && . ./.env && set +a && CELERY_TASK_ALWAYS_EAGER=1 ./venv/bin/python -c "
from server.main import app
ps = sorted({r.path for r in app.routes if 'versions' in getattr(r,'path','')})
print(ps)
assert '/api/knowledge/{doc_id}/versions' in ps and '/api/knowledge/{doc_id}/versions/{version_id}/restore' in ps, ps
print('version routes mounted')
"
```
Expected: 打印三条版本路由 + `version routes mounted`。

- [ ] **Step 3: 提交**

```bash
git add server/api/knowledge.py
git commit -m "$(cat <<'EOF'
feat(knowledge): 版本 REST 端点（list/get/restore）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 后端端到端验证（快照/回滚/裁剪）

**Files:** 临时脚本（验完删）

- [ ] **Step 1: 写验证脚本 `scratchpad/verify_phase2.py`**

`mkdir -p scratchpad` 后：

```python
"""阶段 2 后端验证：编辑存快照、回滚、裁剪 20 版。commit+清理。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from database.db import DB
from database.models import Project, KnowledgeDocument
from server.services import knowledge_service as ks
from server.services import knowledge_version_service as kvs

db = DB(); s = db.session
pid = s.query(Project).order_by(Project.id.asc()).first().id
made = []
try:
    d = ks.create_doc(s, project_id=pid, title="ZZ版本文档", content_html="<p>v1</p>", include_in_rag=False); s.commit()
    made.append(d.id)
    assert len(kvs.list_versions(s, d.id)) == 0, "新建不产生历史版本"
    print("✓ 新建无历史版本")

    ks.update_doc(s, d, content_html="<p>v2</p>", editor_id=None); s.commit()
    vers = kvs.list_versions(s, d.id)
    assert len(vers) == 1 and vers[0].content_html == "<p>v1</p>", "编辑后应存旧内容 v1"
    print("✓ 编辑存旧内容快照")

    # 无变化的 update 不产生新版本
    ks.update_doc(s, d, content_html="<p>v2</p>"); s.commit()
    assert len(kvs.list_versions(s, d.id)) == 1, "内容没变不应新增版本"
    print("✓ 无变化不产生版本")

    # 回滚到 v1（列表里那一版），当前 v2 会先被存一版
    v1 = kvs.list_versions(s, d.id)[0]
    ks.restore_version(s, d, v1, editor_id=None); s.commit()
    d = ks.get_doc(s, d.id)
    assert d.content_html == "<p>v1</p>", "回滚后正文应为 v1"
    assert len(kvs.list_versions(s, d.id)) == 2, "回滚前应把 v2 也存一版"
    print("✓ 回滚生效且当前入历史")

    # 裁剪：连续编辑 25 次，历史应封顶 20
    for i in range(25):
        ks.update_doc(s, d, content_html=f"<p>e{i}</p>"); s.flush()
    s.commit()
    assert len(kvs.list_versions(s, d.id)) == kvs.MAX_VERSIONS, f"应裁剪到 {kvs.MAX_VERSIONS}"
    print(f"✓ 版本裁剪到 {kvs.MAX_VERSIONS}")
    print("ALL GREEN")
finally:
    for did in made:
        x = s.query(KnowledgeDocument).filter(KnowledgeDocument.id == did).first()
        if x: ks.delete_doc(s, x)
    s.commit(); db.close()
```

- [ ] **Step 2: 跑**

Run: `set -a && . ./.env && set +a && ./venv/bin/python scratchpad/verify_phase2.py`
Expected: 5 个 ✓ + `ALL GREEN`。

- [ ] **Step 3: 无污染 + 清理 + 全量单测**

Run:
```bash
set -a && . ./.env && set +a && ./venv/bin/python -c "
from database.db import DB
from database.models import KnowledgeDocument
print('残留 ZZ 文档(应0):', DB().session.query(KnowledgeDocument).filter(KnowledgeDocument.title.like('ZZ%')).count())
"
rm scratchpad/verify_phase2.py
./venv/bin/python -m pytest tests/knowledge/ -q
```
Expected: `残留 ZZ 文档(应0): 0`；pytest 15 passed（11 + 版本 4）。

---

## Task 5: 前端 API + 类型（版本）

**Files:**
- Modify: `frontend/src/types/domain.ts`, `frontend/src/lib/api.ts`

- [ ] **Step 1: 类型（`domain.ts`）**

新增：
```typescript
export interface KnowledgeDocVersion {
  id: number;
  document_id: number;
  title: string;
  editor_id?: number | null;
  created_at?: string | null;
  content_html?: string; // 仅详情返回
}
```

- [ ] **Step 2: API（`api.ts`）**

在 `knowledgeApi` 里（`remove` 之前）加三个方法：
```typescript
  versions(docId: number) {
    return request<KnowledgeDocVersion[]>(`/api/knowledge/${docId}/versions`);
  },
  getVersion(docId: number, versionId: number) {
    return request<KnowledgeDocVersion>(`/api/knowledge/${docId}/versions/${versionId}`);
  },
  restoreVersion(docId: number, versionId: number) {
    return request<KnowledgeDoc>(`/api/knowledge/${docId}/versions/${versionId}/restore`, { method: "POST" });
  },
```
把 `KnowledgeDocVersion` 加入 api.ts 顶部从 `@/types/domain` 的 import 列表。

- [ ] **Step 3: typecheck + 提交**

Run: `cd frontend && npm run typecheck`（通过）
```bash
git add frontend/src/types/domain.ts frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 版本 API + 类型

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: ConfirmDialog 组件 + 替换知识库的 confirm()

**Files:**
- Create: `frontend/src/components/ui/confirm-dialog.tsx`
- Modify: `frontend/src/pages/knowledge/KnowledgeBasePanel.tsx`, `frontend/src/pages/knowledge/KnowledgeFolderTree.tsx`

- [ ] **Step 1: 写通用确认弹窗（基于既有 Dialog）**

创建 `frontend/src/components/ui/confirm-dialog.tsx`：

```tsx
/** 通用确认弹窗——替代原生 confirm()。基于既有 Dialog。 */
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText = "删除",
  destructive = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  confirmText?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onCancel(); }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>取消</Button>
          <Button variant={destructive ? "destructive" : "default"} onClick={onConfirm}>{confirmText}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```
（若 `button` 无 `destructive` variant，则用 `className="bg-destructive text-destructive-foreground hover:bg-destructive/90"` 代替 variant。先看 `@/components/ui/button` 的 variants。）

- [ ] **Step 2: 面板删除改用 ConfirmDialog**

在 `KnowledgeBasePanel.tsx`：import `ConfirmDialog`；加状态 `const [pendingDelete, setPendingDelete] = useState<KnowledgeDoc | null>(null);`。把卡片删除按钮的 onClick 从 `if (confirm(...)) remove.mutate(d.id)` 改为 `setPendingDelete(d)`。在组件返回的末尾（`</div>` 前）加：
```tsx
      <ConfirmDialog
        open={pendingDelete != null}
        title="删除知识文档"
        description={pendingDelete ? `确定删除「${pendingDelete.title}」？此操作不可撤销。` : ""}
        onConfirm={() => { if (pendingDelete) remove.mutate(pendingDelete.id); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />
```

- [ ] **Step 3: 目录树删除改用 ConfirmDialog**

在 `KnowledgeFolderTree.tsx`：import `ConfirmDialog`；加状态 `const [pendingDelete, setPendingDelete] = useState<KnowledgeFolderNode | null>(null);`。把 `onDelete` 改为 `setPendingDelete(f)`（移除其中的 `window.confirm`）。在组件返回最外层 `</div>` 前加：
```tsx
      <ConfirmDialog
        open={pendingDelete != null}
        title="删除目录"
        description={pendingDelete ? `删除目录「${pendingDelete.name}」？其中的文档与子目录会上移到父级，不会被删除。` : ""}
        onConfirm={() => { if (pendingDelete) removeFolder.mutate(pendingDelete.id); setPendingDelete(null); }}
        onCancel={() => setPendingDelete(null)}
      />
```
（注意：`KnowledgeFolderTree` 顶层是 `<div className="w-64 ...">`，ConfirmDialog 放在它内部末尾即可。）

- [ ] **Step 4: typecheck + lint（本阶段文件）+ 提交**

Run:
```bash
cd frontend && npm run typecheck && npx eslint src/components/ui/confirm-dialog.tsx src/pages/knowledge/KnowledgeBasePanel.tsx src/pages/knowledge/KnowledgeFolderTree.tsx --max-warnings 0
```
Expected: typecheck 通过；eslint 这三个文件 0 warning。
```bash
git add frontend/src/components/ui/confirm-dialog.tsx frontend/src/pages/knowledge/KnowledgeBasePanel.tsx frontend/src/pages/knowledge/KnowledgeFolderTree.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 删除确认改用 ConfirmDialog（替代原生 confirm）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 阅读抽屉重写（TOC + 字号 + 版本历史）

**Files:** Modify（重写）`frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx`

- [ ] **Step 1: 整体替换为：**

```tsx
/** 知识库文档预览抽屉：阅读模式(正文+TOC大纲+字号) / 历史模式(版本列表+回滚)。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, History, Pencil, RotateCcw, Sparkles, Type } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SideDrawer } from "@/components/ui/side-drawer";
import { RichTextViewer } from "@/components/editor/RichTextViewer";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError, knowledgeApi } from "@/lib/api";
import { KNOWLEDGE_CONTEXT_TYPES, type KnowledgeDocVersion } from "@/types/domain";

const TYPE_LABELS = new Map<string, string>(KNOWLEDGE_CONTEXT_TYPES.map((t) => [t.value, t.label]));
const FONT_PX = [14, 15.5, 17.5];

interface TocItem { id: string; text: string; level: number }

export function KnowledgeDocViewDrawer({
  open,
  docId,
  moduleNames,
  onClose,
  onEdit,
}: {
  open: boolean;
  docId: number | null;
  moduleNames: Map<number, string>;
  onClose: () => void;
  onEdit: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"read" | "history">("read");
  const [fontIdx, setFontIdx] = useState(1);
  const [toc, setToc] = useState<TocItem[]>([]);
  const [previewVersionId, setPreviewVersionId] = useState<number | null>(null);
  const [pendingRestore, setPendingRestore] = useState<KnowledgeDocVersion | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // 抽屉每次打开/换文档，回到阅读模式
  useEffect(() => { if (open) { setMode("read"); setPreviewVersionId(null); } }, [open, docId]);

  const detailQuery = useQuery({
    queryKey: ["knowledge", "detail", docId],
    queryFn: () => knowledgeApi.get(docId as number),
    enabled: open && docId != null,
  });
  const doc = detailQuery.data;

  const versionsQuery = useQuery({
    queryKey: ["knowledge", "versions", docId],
    queryFn: () => knowledgeApi.versions(docId as number),
    enabled: open && docId != null && mode === "history",
  });
  const previewQuery = useQuery({
    queryKey: ["knowledge", "version", docId, previewVersionId],
    queryFn: () => knowledgeApi.getVersion(docId as number, previewVersionId as number),
    enabled: previewVersionId != null,
  });

  const restore = useMutation({
    mutationFn: (vid: number) => knowledgeApi.restoreVersion(docId as number, vid),
    onSuccess: () => {
      toast.success("已回滚到该版本");
      queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      setMode("read"); setPreviewVersionId(null);
    },
    onError: (e) => toast.error((e as ApiError).message),
  });

  // 正文渲染后扫描标题生成 TOC（对 editor 渲染的 DOM 直接查询，稳）
  useEffect(() => {
    if (mode !== "read" || !doc) { setToc([]); return; }
    const el = contentRef.current;
    if (!el) return;
    const timer = window.setTimeout(() => {
      const hs = Array.from(el.querySelectorAll("h1,h2,h3")) as HTMLElement[];
      const items: TocItem[] = hs.map((h, i) => {
        h.id = `kb-toc-${i}`;
        h.style.scrollMarginTop = "8px";
        return { id: h.id, text: (h.textContent || `小节 ${i + 1}`).trim(), level: Number(h.tagName[1]) };
      });
      setToc(items);
    }, 150);
    return () => window.clearTimeout(timer);
  }, [doc?.content_html, mode, doc]);

  const scrollTo = (id: string) =>
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const metaBar = useMemo(() => doc && (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="rounded bg-muted px-1.5 py-0.5">{TYPE_LABELS.get(doc.context_type) ?? doc.context_type}</span>
      <span>模块：{doc.module_id != null ? moduleNames.get(doc.module_id) ?? "—" : "根级"}</span>
      {doc.updated_at ? <span>更新：{doc.updated_at.slice(0, 16).replace("T", " ")}</span> : null}
      {doc.include_in_rag ? (
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700">
          <Sparkles className="h-3 w-3" />已入库 AI
        </span>
      ) : (
        <span className="rounded-full border bg-muted px-2 py-0.5 font-medium">仅人读</span>
      )}
    </div>
  ), [doc, moduleNames]);

  return (
    <SideDrawer
      open={open}
      onClose={onClose}
      storageKey="knowledge-view-drawer-width"
      defaultWidth={860}
      minWidth={620}
      title={<><BookOpen className="h-[17px] w-[17px] text-primary" /><span className="truncate">{doc?.title ?? "知识文档"}</span></>}
      footer={
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <Button variant={mode === "read" ? "secondary" : "ghost"} size="sm" onClick={() => setMode("read")}>
              <BookOpen className="h-4 w-4 mr-1" />阅读
            </Button>
            <Button variant={mode === "history" ? "secondary" : "ghost"} size="sm" onClick={() => { setMode("history"); setPreviewVersionId(null); }}>
              <History className="h-4 w-4 mr-1" />历史版本
            </Button>
            {mode === "read" && (
              <Button variant="ghost" size="sm" title="字号" onClick={() => setFontIdx((i) => (i + 1) % FONT_PX.length)}>
                <Type className="h-4 w-4 mr-1" />字号
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>关闭</Button>
            <Button onClick={() => doc && onEdit(doc.id)} disabled={!doc}><Pencil className="h-4 w-4 mr-1" />编辑</Button>
          </div>
        </div>
      }
    >
      {detailQuery.isLoading || !doc ? (
        <div className="flex-1 py-16 text-center text-sm text-muted-foreground">加载中…</div>
      ) : mode === "read" ? (
        <div className="flex-1 overflow-hidden flex">
          <div ref={contentRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-4" style={{ fontSize: FONT_PX[fontIdx] }}>
            {metaBar}
            <RichTextViewer source={doc.content_html ?? ""} />
          </div>
          {toc.length > 0 && (
            <nav className="w-52 shrink-0 border-l overflow-y-auto p-3">
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">本页目录</div>
              <div className="space-y-0.5">
                {toc.map((t) => (
                  <button key={t.id} onClick={() => scrollTo(t.id)}
                    className="block w-full truncate text-left text-xs text-muted-foreground hover:text-foreground py-1 border-l-2 border-transparent hover:border-primary pl-2"
                    style={{ paddingLeft: 8 + (t.level - 1) * 10 }}>
                    {t.text}
                  </button>
                ))}
              </div>
            </nav>
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-hidden flex">
          <div className="w-56 shrink-0 border-r overflow-y-auto p-2">
            {versionsQuery.isLoading ? (
              <div className="p-3 text-xs text-muted-foreground">加载中…</div>
            ) : (versionsQuery.data ?? []).length === 0 ? (
              <div className="p-3 text-xs text-muted-foreground">暂无历史版本（编辑过才会有）</div>
            ) : (
              (versionsQuery.data ?? []).map((v) => (
                <button key={v.id} onClick={() => setPreviewVersionId(v.id)}
                  className={`block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-muted ${previewVersionId === v.id ? "bg-primary/10 text-primary" : ""}`}>
                  <div className="truncate font-medium">{v.title}</div>
                  <div className="text-[11px] text-muted-foreground tabular-nums">{v.created_at ? v.created_at.slice(0, 16).replace("T", " ") : ""}</div>
                </button>
              ))
            )}
          </div>
          <div className="flex-1 overflow-y-auto px-5 py-4">
            {previewVersionId == null ? (
              <div className="py-16 text-center text-sm text-muted-foreground">← 选择一个历史版本预览</div>
            ) : previewQuery.isLoading || !previewQuery.data ? (
              <div className="py-16 text-center text-sm text-muted-foreground">加载中…</div>
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">预览历史版本 · {previewQuery.data.created_at?.slice(0, 16).replace("T", " ")}</span>
                  <Button size="sm" variant="outline" onClick={() => setPendingRestore(previewQuery.data!)}>
                    <RotateCcw className="h-4 w-4 mr-1" />回滚到此版本
                  </Button>
                </div>
                <RichTextViewer source={previewQuery.data.content_html ?? ""} />
              </>
            )}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={pendingRestore != null}
        title="回滚到历史版本"
        description={pendingRestore ? `确定把文档回滚到「${pendingRestore.title}」（${pendingRestore.created_at?.slice(0, 16).replace("T", " ")}）？当前内容会先存为一版历史。` : ""}
        confirmText="回滚"
        destructive={false}
        onConfirm={() => { if (pendingRestore) restore.mutate(pendingRestore.id); setPendingRestore(null); }}
        onCancel={() => setPendingRestore(null)}
      />
    </SideDrawer>
  );
}
```

> 注：`SideDrawer` 的子内容根节点原来是 `flex-1 ... overflow-y-auto`；这里改成 `flex-1 overflow-hidden flex` 内部再分左右两栏各自滚动。若 `SideDrawer` 对子节点有特定布局假设（例如要求直接子节点可滚动），先读 `@/components/ui/side-drawer.tsx` 确认其 body 容器是 `flex flex-col`，本结构（一个 `flex-1` 子节点）与原来一致，兼容。

- [ ] **Step 2: typecheck + lint（本文件）**

Run:
```bash
cd frontend && npm run typecheck && npx eslint src/pages/knowledge/KnowledgeDocViewDrawer.tsx --max-warnings 0
```
Expected: 均通过。若 `Button` 无 `secondary`/`outline` variant，改用已有 variant（看 button.tsx）。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/knowledge/KnowledgeDocViewDrawer.tsx
git commit -m "$(cat <<'EOF'
feat(knowledge-fe): 阅读抽屉——TOC大纲+字号 / 版本历史查看回滚

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 构建 + 浏览器核对

- [ ] **Step 1: typecheck + build**

Run: `cd frontend && npm run typecheck && npm run build`
Expected: 通过，产出 `frontend/dist`。

- [ ] **Step 2: 本阶段文件 lint**

Run: `cd frontend && npx eslint src/pages/knowledge/*.tsx src/components/ui/confirm-dialog.tsx --max-warnings 0`
Expected: 0 warning（全仓 lint 的既有 3 条无关警告不算）。

- [ ] **Step 3: 浏览器核对（起预览后端，用户登录看）**

由控制方启动预览后端（EAGER，别的空闲端口，服务本 worktree dist），核对：
1. 打开一篇内容里含 h1/h2/h3 的文档 → 抽屉右侧出现「本页目录」，点条目平滑滚动到对应标题。
2. 点「字号」循环切换正文字号。
3. 编辑保存该文档 → 再打开抽屉「历史版本」→ 左侧列出刚才的旧版本，点击预览其正文，点「回滚到此版本」→ 确认弹窗 → 回滚后正文变回旧版。
4. 删除文档/目录 → 弹出 ConfirmDialog（不再是浏览器原生 confirm）。

---

## 收尾与验收

- [ ] 阅读抽屉：TOC 大纲 + 锚点滚动 + 字号切换可用。
- [ ] 版本历史：编辑存快照、列表、预览、回滚全链路可用，裁剪 20 版。
- [ ] 删除确认统一为 ConfirmDialog。
- [ ] 后端 15 单测通过；前端 typecheck/build 通过、本阶段文件 lint 0 warning。
- [ ] AI 召回不受影响（回滚也走 sync_rag_projection）。

---

## Self-Review 记录

- **Spec 覆盖**：对应 spec 阶段 2「全屏沉浸阅读页 / TOC / 字号宽度 / 卡片列表 / 版本历史 / 删除确认」。按用户决定：阅读=增强抽屉（非全屏），TOC+字号=Task 7；宽度=抽屉本身可拖拽（沿用）；卡片列表阶段 1b 已做；版本历史=Task 1-5+7（列表/查看/回滚，不做 diff）；删除确认=Task 6。
- **占位扫描**：后端全代码；前端新文件（confirm-dialog、drawer 重写）全代码，Panel/FolderTree 给了精确插入片段。无 TODO。
- **类型一致**：`content_changed`/`snapshot`/`list_versions`/`get_version`/`serialize_version`/`restore_version`（service）与 `knowledgeApi.versions/getVersion/restoreVersion`（前端）签名对齐；`KnowledgeDocVersion` 类型一致；`ConfirmDialog` props（open/title/description/confirmText/destructive/onConfirm/onCancel）在三处调用一致。
- **不变量**：回滚走 `restore_version` → `sync_rag_projection`，保持「纳入检索⇒投影同步」；快照仅在 `content_changed` 为真时产生，避免无谓版本。
