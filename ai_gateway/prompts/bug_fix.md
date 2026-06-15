# AI Bug 修复 —— 根据 Bug 描述 + 代码上下文生成修复方案

## 任务

你是一个资深软件工程师。请阅读以下 Bug 描述和项目代码片段，生成修复方案。

## Bug 信息

```
标题：{{BUG_TITLE}}
严重度：{{BUG_SEVERITY}}
描述：{{BUG_DESCRIPTION}}
复现步骤：{{BUG_REPRODUCE_STEPS}}
```

## 相关代码片段（RAG 检索 Top-K）

{{CODE_CONTEXT}}

## 要求

1. 分析 Bug 根因
2. 确定需要修改的文件
3. 对每个文件，描述具体的改动。**不要用行号定位，用文件中已有的代码片段作为锚点**。

## 输出格式（严格 JSON）

```json
{
  "fix_description": "修复了 XXX 问题，原因是 YYY，修改了 ZZZ（中文，简洁）",
  "files_changed": ["path/to/file1.tsx"],
  "changes": [
    {
      "file": "frontend/src/pages/tasks/TaskDetailPage.tsx",
      "action": "insert_after",
      "after_text": "        </CardContent>\n      </Card>",
      "code": "      {/* 复现步骤 */}\n      {task.metadata?.reproduce_steps && (\n        <Card>\n          ...\n        </Card>\n      )}\n"
    },
    {
      "file": "server/api/some.py",
      "action": "replace",
      "find_text": "def old_function():\n    pass",
      "code": "def new_function():\n    return True\n"
    }
  ]
}
```

### changes 字段说明

- `action` 必须是以下之一：
  - `insert_after` —— 在 `after_text` 匹配行之后插入 `code`
  - `insert_before` —— 在 `before_text` 匹配行之前插入 `code`
  - `replace` —— 把 `find_text` 匹配的内容替换为 `code`
  - `append` —— 在文件末尾追加 `code`
- `after_text` / `before_text` / `find_text` 必须是文件中**唯一**的代码片段。选择有辨识度的上下文（至少 2-3 行）
- `code` 应包含完整的代码行，保持原有缩进风格

注意：
- 如果无法从代码中确定修复方案，`files_changed` 为空数组，`fix_description` 说明原因
- 不要生成不存在的文件路径，只修改已有文件
