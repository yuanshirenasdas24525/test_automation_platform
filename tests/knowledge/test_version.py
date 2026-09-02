"""知识库版本 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_version_service as kvs


def test_content_changed_true_on_title_change():
    assert kvs.content_changed("旧标题", "<p>a</p>", "新标题", None) is True


def test_content_changed_true_on_html_change():
    assert kvs.content_changed("t", "<p>a</p>", None, "<p>b</p>") is True


def test_content_changed_false_when_same_or_none():
    assert kvs.content_changed("t", "<p>a</p>", None, None) is False
    assert kvs.content_changed("t", "<p>a</p>", "t", "<p>a</p>") is False


def test_content_changed_handles_none_old_html():
    assert kvs.content_changed("t", None, None, "") is False
    assert kvs.content_changed("t", None, None, "<p>x</p>") is True
