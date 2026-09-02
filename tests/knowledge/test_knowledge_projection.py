"""知识库 阶段 0 纯函数单测：不依赖 DB。"""
from server.services import knowledge_service as ks


def test_html_to_text_strips_tags():
    assert ks.html_to_text("<p>登录<b>约定</b></p>") == "登录约定"


def test_html_to_text_handles_escaped_html():
    # 被转义存储的 HTML 也要还原成纯文本
    assert ks.html_to_text("&lt;p&gt;订单&lt;/p&gt;") == "订单"


def test_html_to_text_empty():
    assert ks.html_to_text("") == ""
    assert ks.html_to_text(None) == ""


def test_projection_fields_maps_doc_to_context_kwargs():
    # 投影用的字段映射：从文档快照 dict 得到 project_contexts 的写入字段
    doc = {
        "project_id": 7,
        "module_id": 3,
        "title": "登录模块接口约定",
        "context_type": "api_contract",
        "content": "统一鉴权走 JWT",
        "include_in_rag": True,
    }
    fields = ks.projection_fields(doc)
    assert fields["project_id"] == 7
    assert fields["module_id"] == 3
    assert fields["source_type"] == "knowledge"
    assert fields["context_type"] == "api_contract"
    assert fields["content"] == "统一鉴权走 JWT"
    assert fields["summary"] == "统一鉴权走 JWT"
    assert fields["importance"] > 0            # 纳入检索


def test_projection_fields_off_when_not_in_rag():
    doc = {"project_id": 1, "module_id": None, "title": "t",
           "context_type": "term_definition", "content": "x", "include_in_rag": False}
    fields = ks.projection_fields(doc)
    assert fields["importance"] == 0           # 不参与 AI 检索
    assert fields["keywords"] == []
