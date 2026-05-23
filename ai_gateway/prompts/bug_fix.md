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
2. 生成修复方案的 unified diff（请严格使用 `diff --git` 统一格式）
3. 写出修复说明（中文，简洁）

## 输出格式（严格 JSON）

```json
{
  "fix_description": "修复了 XXX 问题，原因是 YYY，修改了 ZZZ",
  "files_changed": ["path/to/file1.py", "path/to/file2.ts"],
  "diff": "diff --git a/path/to/file1.py b/path/to/file1.py\nindex ...\n--- a/path/to/file1.py\n+++ b/path/to/file1.py\n@@ -1,5 +1,7 @@\n ..."
}
```

注意：
- diff 字段必须是有效的 unified diff 格式，可以直接通过 `patch -p1` 应用
- 如果无法从代码中确定修复方案，files_changed 为空数组，diff 为空字符串，fix_description 说明原因
- 不要生成不存在的文件路径，只修改已有文件
