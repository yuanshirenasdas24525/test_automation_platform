from __future__ import annotations

import hashlib
import json
import logging
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from database.schemas.ai_config import PROVIDER_CLAUDE_CODE, PROVIDER_CODEX_CLI, AiModelConfig

logger = logging.getLogger(__name__)


CLI_CASE_PROVIDERS = {PROVIDER_CODEX_CLI, PROVIDER_CLAUDE_CODE}


def is_cli_case_provider(provider: str) -> bool:
    """判断模型配置是否是本地 CLI Agent。"""
    return provider in CLI_CASE_PROVIDERS


def default_cli_command(cfg: AiModelConfig) -> list[str]:
    """返回内置 CLI Agent 的默认命令。prompt 统一从 stdin 喂入。"""
    command = (cfg.extra or {}).get("command")
    if isinstance(command, str) and command.strip():
        return shlex.split(command)

    if cfg.provider == PROVIDER_CODEX_CLI:
        cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral"]
        if cfg.model.strip():
            cmd.extend(["--model", cfg.model.strip()])
        cmd.append("-")
        return cmd

    if cfg.provider == PROVIDER_CLAUDE_CODE:
        cmd = ["claude", "-p", "--output-format", "text"]
        if cfg.model.strip():
            cmd.extend(["--model", cfg.model.strip()])
        return cmd

    raise ValueError(f"不支持的 CLI provider: {cfg.provider}")


def check_cli_agent(cfg: AiModelConfig) -> dict[str, Any]:
    """检测 CLI 是否在 PATH 上，并做一次最小非交互调用。

    只跑 --version 会把"命令存在"误判为"会员登录可用"。Claude Code / Codex
    真正查漏时走的是非交互 print/exec 模式，必须在这里提前验证同一条路径。
    """
    cmd = default_cli_command(cfg)
    executable = cmd[0]
    resolved = shutil.which(executable)
    if not resolved:
        return {
            "ok": False,
            "sample": "",
            "error": f"未找到命令：{executable}，请先安装并登录对应 CLI",
        }

    version_cmd = [resolved, "--version"]
    try:
        proc = subprocess.run(
            version_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        sample = (proc.stdout or proc.stderr or resolved).strip()
    except Exception as exc:  # noqa: BLE001
        sample = resolved
        return {
            "ok": True,
            "sample": f"{sample}（版本检测失败：{exc}）",
            "error": None,
        }

    if proc.returncode != 0:
        detail = _command_error_detail(proc)
        return {
            "ok": False,
            "sample": sample[:200] or resolved,
            "error": f"{executable} 版本检测失败：{detail}",
        }

    smoke_prompt = '只输出一个 JSON 对象：{"ok": true}'
    try:
        smoke = subprocess.run(
            cmd,
            input=smoke_prompt,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "sample": sample[:200] or resolved,
            "error": "CLI 非交互调用超时，请检查是否需要重新登录或授权",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "sample": sample[:200] or resolved,
            "error": f"CLI 非交互调用失败：{exc}",
        }

    if smoke.returncode != 0:
        detail = _command_error_detail(smoke)
        return {
            "ok": False,
            "sample": sample[:200] or resolved,
            "error": _friendly_cli_error(executable, detail),
        }
    if not (smoke.stdout or "").strip():
        return {
            "ok": False,
            "sample": sample[:200] or resolved,
            "error": f"CLI 非交互调用没有输出：{_command_error_detail(smoke)}",
        }

    return {
        "ok": True,
        "sample": (smoke.stdout or sample or resolved).strip()[:200],
        "error": None,
    }


def build_case_enhancement_prompt(
    *,
    module_name: str,
    mode: str,
    digest: str,
    requirement_text: str,
    existing_case_names: list[str],
    cases: list[dict[str, Any]],
    target_extra_count: int,
) -> str:
    """构建给 Codex/Claude Code 的用例补全审稿 prompt。"""
    case_json = json.dumps(cases, ensure_ascii=False, indent=2)
    existing_block = "\n".join(f"- {name}" for name in existing_case_names[:300]) or "（无）"
    return f"""你是一名资深测试架构师，正在审查 AI 生成的测试用例草稿。

# 目标

请基于需求、模块上下文、已有用例和草稿，用更高标准做「高级补全」：
1. 找出遗漏的高风险场景、边界值、异常流、权限、安全、并发、兼容性、跨模块联动。
2. 删除重复、空泛、低价值的草稿。
3. 修正步骤和预期，使其可执行、可验收。
4. 在原草稿基础上补充约 {target_extra_count} 条高价值用例；如果草稿已经很完整，可以少补。
5. 不要凭空捏造业务规则；不确定的地方写成可验证的业务假设。

# 输出要求

只输出一个合法 JSON 对象，不要 Markdown，不要解释文字。结构必须是：

{{
  "summary": "本次补全摘要",
  "quality_score": 0,
  "issues_found": ["发现的问题"],
  "cases": [
    {{
      "name": "用例名称",
      "preconditions": ["前置条件"],
      "steps": ["步骤"],
      "expected": ["预期结果"],
      "after": "建议插入在哪条已有用例之后；没有则空字符串"
    }}
  ]
}}

cases 字段必须是最终建议保留和新增的完整列表。字段名必须严格使用 name/preconditions/steps/expected/after。
如果用例类型是 interface，并且原草稿中有 method/path/headers/body/extract/assertion/sql/requests 字段，必须尽量保留并补全这些字段；新增接口用例也应给出 method/path/body/assertion。

# 模块

{module_name}

# 用例类型

{mode}

# 需求摘要

{digest or "（无）"}

# 原始需求/补充材料

{requirement_text or "（无）"}

# 模块已有用例名（避免重复）

{existing_block}

# 待增强草稿 JSON

{case_json}
"""


def build_outline_gap_prompt(
    *,
    module_name: str,
    mode: str,
    digest: str,
    requirement_text: str,
    existing_points: list[dict[str, Any]],
    existing_case_names: list[str],
    target_extra_count: int = 20,
) -> str:
    """构建给 Codex/Claude Code 的大纲查漏 prompt。"""
    existing_points_block = (
        "\n".join(
            f"- [{p.get('category') or '未分类'}] {p.get('title')}"
            for p in existing_points[:500]
        )
        or "（无）"
    )
    existing_cases_block = "\n".join(f"- {name}" for name in existing_case_names[:300]) or "（无）"
    return f"""你是一名资深测试架构师，正在为测试点大纲做「查漏补缺」。

# 目标

请基于原始需求、需求摘要、已有测试点、模块已有用例，找出真正遗漏的高价值测试点：
1. 优先补充高风险场景、边界值、异常流、权限、安全、数据链路、跨模块联动。
2. 如果是 interface，请重点核对接口、参数、鉴权、越权、响应校验、状态流转、幂等、并发。
3. 不要重复已有测试点或已有用例。
4. 不要凭空捏造业务规则；不确定的地方写成可验证的业务假设。
5. 最多补充 {max(1, min(target_extra_count, 50))} 个测试点；如果已经完整，可以返回空数组。

# 输出要求

只输出一个合法 JSON 对象，不要 Markdown，不要解释文字。结构必须是：

{{
  "points": [
    {{
      "title": "测试点标题",
      "category": "正常/异常/边界/权限/安全/场景/参数校验/兼容/其它"
    }}
  ]
}}

# 模块

{module_name}

# 用例类型

{mode}

# 需求摘要

{digest or "（无）"}

# 原始需求/接口文档

{requirement_text or "（未提供原始文档，只能依据摘要和已有测试点排查）"}

# 已有测试点

{existing_points_block}

# 模块已有用例名

{existing_cases_block}
"""


def run_cli_case_enhancement(
    *,
    cfg: AiModelConfig,
    prompt: str,
    timeout: int,
) -> dict[str, Any]:
    """调用本地 CLI Agent 并解析其 JSON 输出。"""
    cmd = default_cli_command(cfg)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    logger.info("[case_enhance] running %s command=%s", cfg.provider, cmd[:4])

    with tempfile.TemporaryDirectory(prefix="case_enhance_") as tmp:
        workdir = Path(tmp)
        (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"CLI Agent 执行超时（{timeout}s）") from exc

    raw = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = _command_error_detail(proc)
        raise RuntimeError(
            f"CLI Agent 返回非 0：{proc.returncode}，{_friendly_cli_error(cmd[0], detail)[:800]}"
        )
    if not raw:
        raise ValueError(f"CLI Agent 未输出内容：{stderr[:800]}")

    parsed = extract_json_object(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"CLI Agent 输出不是 JSON 对象：{raw[:500]}")

    return {
        "raw": raw,
        "parsed": parsed,
        "prompt_hash": prompt_hash,
    }


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """从 CLI 输出中抽取 JSON 对象。"""
    text = raw.strip()
    if text.startswith("```"):
        import re

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S | re.I)
        if m:
            text = m.group(1)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _command_error_detail(proc: subprocess.CompletedProcess[str]) -> str:
    """拼接 CLI 的 stderr/stdout，避免真实错误落在 stdout 时被吞掉。"""
    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    detail = "\n".join(part for part in [stderr, stdout] if part)
    return detail[:1200] or "无错误输出"


def _friendly_cli_error(executable: str, detail: str) -> str:
    """把 CLI 常见的登录或组织权限错误转换成可直接操作的提示。"""
    normalized = detail.lower()
    if executable == "claude" and (
        "does not have access to claude" in normalized
        or "please login again" in normalized
        or "authentication" in normalized
        or "not logged in" in normalized
    ):
        return (
            "Claude Code 登录态不可用或当前组织无 Claude 权限。请在运行后端的同一账号下执行 "
            "`claude auth status` 检查；若仍提示无权限，执行 `claude auth logout` 后重新登录有权限的账号。"
            f" 原始错误：{detail[:500]}"
        )
    return f"CLI 非交互调用失败：{detail}"
