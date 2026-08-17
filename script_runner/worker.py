"""脚本库子进程入口。

协议只有一进一出：stdin 读取一份 JSON，stdout 输出一份 JSON。脚本产生的
``print`` 会被收集到响应的 ``stdout``，不会破坏协议。这个模块刻意只使用标准库，
也不导入平台的 server/database/runners 代码。
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import sys
import traceback
from typing import Any


def _load_crypto_compat() -> Any:
    """按文件加载独立兼容模块，不把项目根加入 sys.path。"""
    path = Path(__file__).resolve().with_name("crypto_compat.py")
    spec = importlib.util.spec_from_file_location("tap_script_crypto", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载脚本加密兼容模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_handler(request: dict[str, Any]) -> Any:
    dependency_path = request.get("dependency_path")
    if dependency_path:
        resolved = Path(str(dependency_path)).resolve()
        if resolved.is_dir():
            sys.path.insert(0, str(resolved))
    code = str(request.get("code") or "")
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "__name__": "__project_script__",
        "__package__": None,
    }
    if "crypto." in code:
        namespace["crypto"] = _load_crypto_compat()
    exec(compile(code, "<script_library>", "exec"), namespace, namespace)  # noqa: S102
    handler = namespace.get("handler")
    if not callable(handler):
        raise ValueError("脚本必须定义可调用的 handler")

    kind = str(request.get("kind") or "function")
    variables = dict(request.get("vars") or {})
    config = dict(request.get("config") or {})
    if kind == "function":
        result = handler(
            *(request.get("args") or []),
            vars=variables,
            ctx=dict(request.get("context") or {}),
            **(request.get("kwargs") or {}),
        )
    elif kind == "crypto_request":
        result = handler(
            dict(request.get("headers") or {}),
            request.get("body"),
            config,
            vars=variables,
        )
    elif kind == "crypto_response":
        result = handler(request.get("body"), config, vars=variables)
    elif kind == "workflow":
        result = handler(request.get("body"), vars=variables, config=config)
    else:
        raise ValueError(f"不支持的脚本类型：{kind}")

    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def execute(request: dict[str, Any]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = _call_handler(request)
        # 在子进程内先验证能否 JSON 化，避免父进程拿到半截协议。
        json.dumps(result, ensure_ascii=False)
        return {
            "status": "passed",
            "result": result,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
    except AssertionError as exc:
        return {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc) or "AssertionError",
            "traceback": traceback.format_exc(),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise TypeError("脚本执行请求必须是 JSON 对象")
        response = execute(request)
    except Exception as exc:  # noqa: BLE001
        response = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
