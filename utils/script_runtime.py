from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform as stdlib_platform
import signal
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from utils.logger import LOGGER


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_WORKER = _PROJECT_ROOT / "script_runner" / "worker.py"
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 30
_SCRIPT_DEPENDENCIES_ROOT = _PROJECT_ROOT / "data" / "script_runtimes"


class ScriptRuntimeError(RuntimeError):
    """脚本独立运行时错误。"""


class ScriptTimeoutError(ScriptRuntimeError):
    """脚本超过允许执行时间。"""


class ScriptAssertionError(AssertionError):
    """脚本主动断言失败。"""


def run_script(
    code: str,
    *,
    kind: str,
    args: list[Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
    body: Any = None,
    config: dict[str, Any] | None = None,
    vars: dict[str, Any] | None = None,  # noqa: A002 - 业务语义就是变量池
    ctx: Any = None,
    timeout: int | float | None = None,
    requirements: list[str] | None = None,
) -> Any:
    """在独立 Python 进程中执行脚本。

    平台进程只负责发送/接收 JSON，不会 import 或 exec 项目脚本。不同 kind 的调用约定：
      - function: handler(*args, vars=vars, ctx=ctx, **kwargs)
      - crypto_request: handler(headers, body, config, vars=vars)
      - crypto_response: handler(response_body, config, vars=vars)
      - workflow: handler(input, vars=vars, config=config)

    ``workflow`` 返回 ``{"variables": {...}}`` 时，由 ScriptStepRunner 写回用例变量池。
    脚本可以 import 当前脚本运行环境已经安装的任意第三方包。
    """
    request = {
        "code": code,
        "kind": kind,
        "args": deepcopy(args or []),
        "kwargs": deepcopy(kwargs or {}),
        "headers": deepcopy(headers or {}),
        "body": deepcopy(body),
        "config": deepcopy(config or {}),
        "vars": _json_safe(vars or {}),
        "context": _json_safe(_context_payload(ctx)),
    }
    dependency_path = _ensure_dependencies(requirements or [])
    if dependency_path is not None:
        request["dependency_path"] = str(dependency_path)
    response = _run_worker(request, timeout=timeout)
    status = response.get("status")
    if status == "passed":
        return response.get("result")
    message = _format_worker_error(response)
    if status == "failed":
        raise ScriptAssertionError(message)
    raise ScriptRuntimeError(message)


def run_named_script(
    name: str,
    *,
    kind: str,
    project_id: int | None = None,
    **kwargs: Any,
) -> tuple[bool, Any]:
    """按名称执行启用的页面脚本。

    查找优先级：项目脚本 > 全局脚本。找不到返回 (False, None)，调用方可继续走
    文件内置函数兜底。
    """
    row = _find_script(name=name, kind=kind, project_id=project_id)
    if row is None:
        return False, None
    return True, run_script(
        row.code,
        kind=row.kind,
        requirements=list(row.requirements or []),
        **kwargs,
    )


def _ensure_dependencies(requirements: list[str]) -> Path | None:
    """按依赖集合建立可复用的隔离目录，不修改平台 requirements.txt。"""
    normalized = sorted({str(item).strip() for item in requirements if str(item).strip()})
    if not normalized:
        return None
    for requirement in normalized:
        if requirement.startswith("-") or any(char in requirement for char in "\r\n\x00"):
            raise ScriptRuntimeError(f"非法脚本依赖：{requirement!r}")
    runtime_key = "\n".join([
        sys.implementation.cache_tag or "python",
        stdlib_platform.system(),
        stdlib_platform.machine(),
        *normalized,
    ])
    digest = hashlib.sha256(runtime_key.encode("utf-8")).hexdigest()[:20]
    target = _SCRIPT_DEPENDENCIES_ROOT / digest / "site-packages"
    marker = target.parent / "ready.json"
    lock_path = target.parent / ".install.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if marker.is_file():
            return target
        target.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(target),
            *normalized,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            cwd=str(target.parent),
            env=_isolated_environment(),
        )
        if completed.returncode != 0:
            raise ScriptRuntimeError(
                "脚本依赖安装失败："
                f"{completed.stderr.strip()[-2000:] or completed.stdout.strip()[-2000:]}"
            )
        marker.write_text(
            json.dumps({"requirements": normalized}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return target


def _run_worker(request: dict[str, Any], *, timeout: int | float | None) -> dict[str, Any]:
    """启动一次性脚本进程并返回结构化结果。"""
    if not _SCRIPT_WORKER.is_file():
        raise ScriptRuntimeError(f"脚本运行器不存在：{_SCRIPT_WORKER}")
    seconds = float(timeout or DEFAULT_SCRIPT_TIMEOUT_SECONDS)
    if seconds <= 0:
        seconds = DEFAULT_SCRIPT_TIMEOUT_SECONDS
    payload = json.dumps(request, ensure_ascii=False)
    env = _isolated_environment()
    with tempfile.TemporaryDirectory(prefix="tap-script-") as workdir:
        process = subprocess.Popen(
            [sys.executable, "-I", str(_SCRIPT_WORKER)],
            cwd=workdir,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(payload, timeout=seconds)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            process.communicate()
            raise ScriptTimeoutError(f"脚本执行超过 {seconds:g} 秒，已终止独立进程") from exc
    if process.returncode != 0:
        raise ScriptRuntimeError(
            f"脚本运行器异常退出（code={process.returncode}）：{stderr.strip()[:1000]}"
        )
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ScriptRuntimeError(
            f"脚本运行器返回了无效 JSON：{stdout[:1000]!r}；stderr={stderr[:1000]!r}"
        ) from exc
    if not isinstance(response, dict):
        raise ScriptRuntimeError("脚本运行器响应必须是 JSON 对象")
    return response


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


def _isolated_environment() -> dict[str, str]:
    """只把运行脚本需要的通用环境传给子进程，不泄露平台 DB/JWT 配置。"""
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _context_payload(ctx: Any) -> dict[str, Any]:
    if ctx is None:
        return {}
    if isinstance(ctx, dict):
        return ctx
    return {
        "vars": getattr(ctx, "vars", {}),
        "records": getattr(ctx, "records", {}),
    }


def _json_safe(value: Any) -> Any:
    """递归保留可序列化数据；浏览器/数据库连接等运行对象不会跨进程泄露。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items() if _is_json_safe(item)}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value if _is_json_safe(item)]
    return str(value)


def _is_json_safe(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
        return True
    except (TypeError, ValueError):
        return isinstance(value, (dict, list, tuple, set))


def _format_worker_error(response: dict[str, Any]) -> str:
    error_type = str(response.get("error_type") or "ScriptError")
    message = str(response.get("error") or "脚本执行失败")
    stderr = str(response.get("stderr") or "").strip()
    suffix = f"；stderr={stderr[:500]}" if stderr else ""
    return f"{error_type}: {message}{suffix}"


def _find_script(name: str, kind: str, project_id: int | None):
    try:
        from database.db import DB
        from database.models import ScriptStore
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("查询页面脚本失败 name=%s kind=%s project_id=%s: %s", name, kind, project_id, exc)
        return None

    db = None
    try:
        db = DB()
        base = db.session.query(ScriptStore).filter(
            ScriptStore.name == name,
            ScriptStore.kind == kind,
            ScriptStore.enabled.is_(True),
        )
        if project_id is not None:
            project_row = base.filter(ScriptStore.project_id == project_id).first()
            if project_row is not None:
                return project_row
        return base.filter(ScriptStore.project_id.is_(None)).first()
    except Exception:  # noqa: BLE001
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


def list_script_names(kind: str, project_id: int | None = None) -> list[str]:
    """列出当前可用页面脚本名，用于错误提示。"""
    try:
        from database.db import DB
        from database.models import ScriptStore
    except Exception:  # noqa: BLE001
        return []

    db = None
    try:
        db = DB()
        query = db.session.query(ScriptStore.name).filter(
            ScriptStore.kind == kind,
            ScriptStore.enabled.is_(True),
        )
        if project_id is not None:
            query = query.filter(
                (ScriptStore.project_id == project_id) | ScriptStore.project_id.is_(None)
            )
        else:
            query = query.filter(ScriptStore.project_id.is_(None))
        return sorted({row[0] for row in query.all()})
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("列出页面脚本失败 kind=%s project_id=%s: %s", kind, project_id, exc)
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
