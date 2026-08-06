import json

from server.services.doc_ingest import IngestResult, ingest_sources

MINIMAL_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {"/login": {"post": {"summary": "登录", "responses": {"200": {"description": "ok"}}}}},
}


def test_ingest_openapi_json_bytes_builds_contract():
    files = [("api.json", json.dumps(MINIMAL_OPENAPI).encode("utf-8"))]
    res = ingest_sources(files=files, links=[])
    assert isinstance(res, IngestResult)
    ops = res.contract.get("operations") or []
    assert any(o.get("path") == "/login" and o.get("method", "").lower() == "post" for o in ops)
    assert res.warnings == []


def test_ingest_bad_json_falls_back_to_text_with_warning():
    files = [("junk.json", b"{not json")]
    res = ingest_sources(files=files, links=[])
    assert res.warnings
    assert (res.contract.get("operations") or []) == []
