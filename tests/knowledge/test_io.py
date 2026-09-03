"""知识库导入导出 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_io_service as io


def test_html_to_markdown():
    md = io.html_to_markdown("<h1>标题</h1><p>正文<strong>粗</strong></p>")
    assert "# 标题" in md
    assert "正文" in md and "粗" in md


def test_markdown_to_html():
    html = io.markdown_to_html("# 标题\n\n正文")
    assert "<h1" in html and "标题" in html
    assert "正文" in html


def test_safe_name():
    assert io.safe_name("a/b:c*.md") == "a_b_c_.md"
    assert io.safe_name("") == "doc"
    assert io.safe_name("中文名 ok") == "中文名 ok"


def test_ext_of():
    assert io.ext_of("A.MD") == "md"
    assert io.ext_of("x.docx") == "docx"
    assert io.ext_of("noext") == ""
