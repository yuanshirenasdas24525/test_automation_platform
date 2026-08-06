"""把上传文件 / 链接编排成：接口契约(catalog) + 补充文本 + 警告。

OpenAPI/Swagger(json/yaml) → api_case_contract.build_contract_catalog
其它文档(pdf/docx/md) → doc_parser 抽文本
链接 → 抓取后按同样规则分流
Postman collection 本期：识别到就抽 request 列表塞进文本，识别不了回退纯文本。
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.services.api_case_contract import (
    build_contract_catalog,
    empty_contract_catalog,
    merge_contract_catalogs,
)
from server.services.doc_parser import parse_document

_OPENAPI_SUFFIXES = {".json", ".yaml", ".yml"}
_TEXT_SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".txt"}
_MAX_LINK_BYTES = 5 * 1024 * 1024


@dataclass
class IngestResult:
    contract: dict[str, Any] = field(default_factory=empty_contract_catalog)
    text_blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _load_openapi_document(raw: bytes) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace")
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    try:
        import yaml
        obj = yaml.safe_load(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _catalog_from_openapi_bytes(name: str, raw: bytes, warnings: list[str]) -> dict[str, Any] | None:
    doc = _load_openapi_document(raw)
    if not isinstance(doc, dict):
        warnings.append(f"{name}: 不是合法的 OpenAPI/JSON/YAML，已按纯文本处理")
        return None
    if "paths" not in doc and "openapi" not in doc and "swagger" not in doc:
        warnings.append(f"{name}: 非 OpenAPI 文档，已按纯文本处理")
        return None
    try:
        return build_contract_catalog(doc)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name}: 契约解析失败({exc})，已按纯文本处理")
        return None


def _text_from_bytes(name: str, raw: bytes, warnings: list[str]) -> str | None:
    suffix = Path(name).suffix.lower()
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tf:
            tf.write(raw)
            tf.flush()
            parsed = parse_document(tf.name)
        return (parsed.plain_text or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{name}: 文本解析失败({exc})，已跳过")
        return None


def ingest_sources(
    files: list[tuple[str, bytes]] | None = None,
    links: list[str] | None = None,
) -> IngestResult:
    result = IngestResult()
    catalogs: list[dict[str, Any]] = []

    for name, raw in files or []:
        suffix = Path(name).suffix.lower()
        if suffix in _OPENAPI_SUFFIXES:
            cat = _catalog_from_openapi_bytes(name, raw, result.warnings)
            if cat is not None:
                catalogs.append(cat)
                continue
            txt = raw.decode("utf-8", errors="replace").strip()
            if txt:
                result.text_blocks.append(f"# {name}\n{txt[:20000]}")
        elif suffix in _TEXT_SUFFIXES:
            txt = _text_from_bytes(name, raw, result.warnings)
            if txt:
                result.text_blocks.append(f"# {name}\n{txt[:20000]}")
        else:
            result.warnings.append(f"{name}: 不支持的类型，已跳过")

    for url in links or []:
        _ingest_link(url, catalogs, result)

    if catalogs:
        result.contract = merge_contract_catalogs(catalogs)
    return result


def _ingest_link(url: str, catalogs: list[dict[str, Any]], result: IngestResult) -> None:
    url = (url or "").strip()
    if not url:
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        result.warnings.append(f"{url}: 仅支持 http/https 链接，已跳过")
        return
    if _is_blocked_host(url):
        result.warnings.append(f"{url}: 拒绝访问内网/环回地址")
        return
    try:
        import requests
        resp = requests.get(url, timeout=15, stream=True, allow_redirects=False)
        resp.raise_for_status()
        raw = resp.raw.read(_MAX_LINK_BYTES + 1, decode_content=True)
        if len(raw) > _MAX_LINK_BYTES:
            result.warnings.append(f"{url}: 内容过大，已截断")
            raw = raw[:_MAX_LINK_BYTES]
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"{url}: 抓取失败({exc})，已跳过")
        return
    cat = _catalog_from_openapi_bytes(url, raw, result.warnings)
    if cat is not None:
        catalogs.append(cat)
    else:
        txt = raw.decode("utf-8", errors="replace").strip()
        if txt:
            result.text_blocks.append(f"# {url}\n{txt[:20000]}")


def _is_blocked_host(url: str) -> bool:
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if host in {"localhost", ""}:
        return True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except Exception:  # noqa: BLE001
        return True
    return False
