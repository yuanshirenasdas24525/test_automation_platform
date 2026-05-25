"""RAG 索引器 —— 把 working tree 扫成 chunk 列表。

设计：
1. **白名单扩展名**：只索引常见源码 / 配置 / 文档；binary、generated、依赖目录全跳
2. **大文件跳过**：单文件 > 256KB → 跳（防 README 里塞 base64 / lock 文件）
3. **行窗分块**：默认每 60 行一块、相邻块重叠 10 行 —— 简单稳定，无需 tree-sitter
4. **chunk 不再做摘要**：原文塞 prompt，避免摘要丢上下文
5. **路径锚定**：所有相对路径基于 ``workspace_root``，便于落库后跨机器索引复用

为啥不上 tree-sitter / AST 拆分？
- 起步阶段语言混杂（py/ts/tsx/sql/md/yaml/go/rs/...），加 tree-sitter 等于背 N 个语法包
- LLM 对"60 行连续上下文"消化得也够好；后续测试发现召回不够再升级
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# 扫描白名单 / 黑名单
# ---------------------------------------------------------------------------
# 索引这些扩展名（小写、含点）
INDEXABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # backend
        ".py", ".pyi", ".pyx",
        ".go", ".rs", ".java", ".kt", ".scala", ".rb",
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        ".php", ".swift", ".m", ".mm",
        ".c", ".cc", ".cpp", ".h", ".hpp",
        # web/markup
        ".html", ".htm", ".vue", ".svelte", ".css", ".scss", ".less",
        # configs / data
        ".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".env",
        ".sql", ".graphql", ".gql",
        # shell / make
        ".sh", ".bash", ".zsh", ".fish", ".dockerfile",
    }
)

# 这些目录整个跳（含其后代）
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "bower_components",
        "venv", ".venv", "env", ".env",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "dist", "build", "out", "target", ".next", ".nuxt", ".turbo",
        "coverage", ".coverage", ".nyc_output",
        ".idea", ".vscode",
        "site-packages",
    }
)

# 文件名命中这些子串直接跳（lock / generated）
SKIP_FILENAME_SUBSTRINGS: tuple[str, ...] = (
    ".lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    ".min.js", ".min.css", ".bundle.js",
    "tsconfig.tsbuildinfo",
)

MAX_FILE_BYTES = 256 * 1024     # 256KB 上限
DEFAULT_CHUNK_LINES = 60
DEFAULT_OVERLAP_LINES = 10


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IndexChunk:
    """工作树里的一块代码 —— 落库前的中间形态。

    ``file_path`` 是相对 workspace_root 的 posix-style 路径（落库时跨平台对齐）。
    ``content`` 是这一段的原文，包含末尾换行。
    """
    file_path: str
    chunk_idx: int
    start_line: int   # 1-based
    end_line: int     # 1-based, inclusive
    content: str


# ---------------------------------------------------------------------------
# 主 API
# ---------------------------------------------------------------------------
def scan_workspace(
    workspace_root: Path,
    *,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> Iterator[IndexChunk]:
    """遍历 workspace，按规则筛文件 → 行窗分块，惰性 yield。

    生成器写法是故意的：repo 可能很大，调用方一边 yield 一边算 embedding 落库，
    内存峰值不会随 repo 体积膨胀。
    """
    workspace_root = Path(workspace_root).resolve()
    if not workspace_root.is_dir():
        raise NotADirectoryError(f"workspace_root 不存在或不是目录：{workspace_root}")

    for file_path in _iter_indexable_files(workspace_root):
        try:
            rel = file_path.relative_to(workspace_root).as_posix()
        except ValueError:
            continue
        yield from chunk_file(
            file_path,
            rel_path=rel,
            chunk_lines=chunk_lines,
            overlap_lines=overlap_lines,
        )


def chunk_file(
    file_path: Path,
    *,
    rel_path: str,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    overlap_lines: int = DEFAULT_OVERLAP_LINES,
) -> Iterable[IndexChunk]:
    """按行窗切单个文件；空文件 / 解码失败 → 直接跳。"""
    if chunk_lines <= 0:
        raise ValueError(f"chunk_lines 必须 > 0，当前 {chunk_lines}")
    if overlap_lines < 0 or overlap_lines >= chunk_lines:
        raise ValueError(
            f"overlap_lines 必须满足 0 <= o < chunk_lines；当前 {overlap_lines}"
        )

    # 解码：utf-8 主路径，失败回退 utf-8 errors=replace；二进制内容触发 errors 也无所谓
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
    except OSError:
        return

    lines = text.splitlines(keepends=True)
    if not lines:
        return

    stride = chunk_lines - overlap_lines
    idx = 0
    start = 0
    n = len(lines)
    while start < n:
        end = min(start + chunk_lines, n)
        chunk_text = "".join(lines[start:end])
        # 空白块 / 仅注释空行的块（< 5 个非空字符）跳过
        if len(chunk_text.strip()) < 5:
            start += stride
            idx += 1
            continue
        yield IndexChunk(
            file_path=rel_path,
            chunk_idx=idx,
            start_line=start + 1,
            end_line=end,
            content=chunk_text,
        )
        if end == n:
            break
        start += stride
        idx += 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _iter_indexable_files(root: Path) -> Iterator[Path]:
    """walk root，跳黑名单目录 / 文件，命中白名单扩展名才 yield。"""
    # 用栈模拟 walk，避免 os.walk 的字符串拼接 + 跨平台路径分隔符问题
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                # 隐藏目录 / 文件大多跳；但 .env / .gitignore 等可被白名单收回
                if entry.is_dir() and name in SKIP_DIR_NAMES:
                    continue
                if entry.is_dir():
                    # 默认跳所有 dotdir（避免索引 .next/.turbo 的 cache）
                    continue
            if entry.is_dir():
                if name in SKIP_DIR_NAMES:
                    continue
                stack.append(entry)
                continue
            if not entry.is_file():
                continue
            if _should_skip_filename(name):
                continue
            ext = entry.suffix.lower()
            if ext not in INDEXABLE_EXTENSIONS:
                # 没扩展名的特殊文件：Dockerfile / Makefile 走名字白名单
                if name.lower() not in {"dockerfile", "makefile", "rakefile"}:
                    continue
            try:
                if entry.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield entry


def _should_skip_filename(name: str) -> bool:
    lowered = name.lower()
    return any(sub in lowered for sub in SKIP_FILENAME_SUBSTRINGS)
