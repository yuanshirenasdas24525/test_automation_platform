# Tiptap 集成方案

## 概述

引入 [Tiptap](https://tiptap.dev/)（基于 ProseMirror 的可扩展所见即所得编辑器），替换现有的 `@uiw/react-md-editor` Markdown 编辑器和平凡 Textarea，提升用户编辑体验。

## 包依赖

```json
{
  "@tiptap/extension-code-block-lowlight": "^2.x",
  "@tiptap/extension-highlight": "^2.x",
  "@tiptap/extension-image": "^2.x",
  "@tiptap/extension-link": "^2.x",
  "@tiptap/extension-placeholder": "^2.x",
  "@tiptap/extension-table": "^2.x",
  "@tiptap/extension-table-cell": "^2.x",
  "@tiptap/extension-table-header": "^2.x",
  "@tiptap/extension-table-row": "^2.x",
  "@tiptap/extension-text-align": "^2.x",
  "@tiptap/extension-underline": "^2.x",
  "@tiptap/pm": "^2.x",
  "@tiptap/react": "^2.x",
  "@tiptap/starter-kit": "^2.x",
  "lowlight": "^3.x"
}
```

约 60KB gzip（替换原有的 `@uiw/react-md-editor` ~120KB，净体积减小）。

## 组件设计

### RichTextEditor (`src/components/editor/RichTextEditor.tsx`)

编辑态组件，支持三种工具栏级别：

| 级别 | 可用扩展 | 适用场景 |
|------|----------|----------|
| `full` | 标题/粗斜体/列表/引用/高亮/链接/图片/代码块(高亮)/表格/对齐 | 需求描述、AI分析文档 |
| `minimal` | 粗斜体/列表/引用/链接/代码块 | Bug描述、复现步骤、功能用例 |
| `none` | 无工具栏 | 只读查看 |

### RichTextViewer (`src/components/editor/RichTextViewer.tsx`)

只读态组件，展示 Tiptap 生成的 HTML 内容。
- 空内容显示 "(无描述)" 提示
- 使用 `EditorContent` 的 `readonly` 模式

## 数据兼容策略

**核心原则**：渐进迁移，新写 HTML，旧读兼容。

- **写入**：Tiptap 输出 HTML 字符串，直接存入 `description` 字段（后端 `Text` 列无需改动）
- **读取**：旧 Markdown / 纯文本自动识别，正常渲染
- **历史数据**：打开编辑后自动转为 HTML 并保存
- **后端无需改动**：Pydantic schema 的 `str` 类型完全兼容

## 实施计划

### Phase 1 --- 核心引入

| 文件 | 改动 | 工具栏 |
|------|------|--------|
| `RequirementsPage.tsx` 需求创建/编辑表单 | `MarkdownEditor` → `RichTextEditor` | full |
| `RequirementDetailDrawer.tsx` 需求详情 | `MarkdownView` → `RichTextViewer` | -- |
| `CreateBugModal.tsx` Bug 描述 + 复现步骤 | `Textarea` → `RichTextEditor` | minimal |
| `main.tsx` | Tiptap CSS 替换 md-editor CSS | -- |

### Phase 2 --- 建议跟进

| 文件 | 改动 | 理由 |
|------|------|------|
| `AnalysisDocumentViewerDialog.tsx` | `MarkdownEditor` → `RichTextEditor` (full) | 与需求编辑体验统一 |
| `FunctionalCasesPage.tsx` 前提条件/步骤/预期 | `Textarea` → `RichTextEditor` (minimal) | 步骤需要编号、代码块 |
| `ProjectVersionDetailPage.tsx` 4个备注字段 | `Textarea` → `RichTextEditor` (minimal) | SQL/配置可用代码块 |

### Phase 3 --- 可选

| 文件 | 改动 | 理由 |
|------|------|------|
| 工作台各页面的备注/说明 | 按需替换 | 业务量不大 |
| 4区文档撰写能力 | 内建文档编辑器 | 目前仅链接 |

## 风险与注意事项

1. **XSS 安全**：`RichTextViewer` 使用 Tiptap 内置的 HTML 安全渲染机制
2. **图片存储**：Phase 1 仅支持外部 URL，Phase 2 对接附件上传 API
3. **移动端**：工具栏在窄屏上需做响应式调整
