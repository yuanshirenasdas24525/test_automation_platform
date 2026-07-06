from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import random
import re
import time
import uuid
from copy import deepcopy
from typing import Any

from utils.logger import LOGGER


ALLOWED_MODULES = {
    "base64": base64,
    "hashlib": hashlib,
    "hmac": hmac,
    "json": json,
    "math": math,
    "random": random,
    "re": re,
    "time": time,
    "uuid": uuid,
}

SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "round": round,
    "set": set,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


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
) -> Any:
    """执行页面脚本。

    页面脚本必须定义 handler。不同 kind 的调用约定：
      - function: handler(*args, vars=vars, ctx=ctx, **kwargs)
      - crypto_request: handler(headers, body, config, vars=vars)
      - crypto_response: handler(response_body, config, vars=vars)
    """
    namespace = compile_script(code)
    handler = namespace.get("handler")
    if not callable(handler):
        raise ValueError("脚本必须定义可调用的 handler")

    vars = vars or {}
    config = config or {}
    if kind == "function":
        return handler(*(args or []), vars=vars, ctx=ctx, **(kwargs or {}))
    if kind == "crypto_request":
        return handler(deepcopy(headers or {}), deepcopy(body), dict(config), vars=vars)
    if kind == "crypto_response":
        return handler(deepcopy(body), dict(config), vars=vars)
    raise ValueError(f"不支持的脚本类型：{kind}")


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
    return True, run_script(row.code, kind=row.kind, **kwargs)


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


def compile_script(code: str) -> dict[str, Any]:
    """编译脚本并返回命名空间。"""
    globals_dict: dict[str, Any] = {
        "__builtins__": {**SAFE_BUILTINS, "__import__": _safe_import},
        **ALLOWED_MODULES,
    }
    locals_dict: dict[str, Any] = {}
    exec(compile(code, "<script_store>", "exec"), globals_dict, locals_dict)  # noqa: S102
    return {**globals_dict, **locals_dict}


def _safe_import(name: str, globals=None, locals=None, fromlist=(), level=0):  # noqa: ARG001
    """只允许导入白名单模块。"""
    root = name.split(".", 1)[0]
    if root not in ALLOWED_MODULES:
        raise ImportError(f"页面脚本不允许导入模块：{name}")
    return ALLOWED_MODULES[root]
