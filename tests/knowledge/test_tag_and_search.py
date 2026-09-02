"""标签规范化 + 搜索关键字规范化 纯函数单测。"""
from server.services import knowledge_tag_service as kts
from server.services import knowledge_service as ks


def test_normalize_tag_name_trims_and_caps_length():
    assert kts.normalize_tag_name("  核心链路  ") == "核心链路"
    assert kts.normalize_tag_name("x" * 100) == "x" * 64      # 截到 64
    assert kts.normalize_tag_name("") == ""


def test_dedupe_tag_ids_preserves_order():
    assert kts.dedupe_tag_ids([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_normalize_search_query():
    # 搜索词：去首尾空白；空/纯空白 → None（表示不过滤）
    assert ks.normalize_search_query("  JWT 鉴权 ") == "JWT 鉴权"
    assert ks.normalize_search_query("   ") is None
    assert ks.normalize_search_query(None) is None
