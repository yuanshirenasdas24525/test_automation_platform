"""知识库目录树构建 纯函数单测（不依赖 DB）。"""
from server.services import knowledge_folder_service as kfs


def _f(id, parent_id, name, sort_order=0):
    return {"id": id, "parent_id": parent_id, "name": name, "sort_order": sort_order}


def test_build_tree_nests_children_under_parents():
    rows = [
        _f(1, None, "接口规范", 0),
        _f(2, 1, "登录鉴权", 0),
        _f(3, 1, "订单交易", 1),
        _f(4, None, "业务规则", 1),
    ]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [1, 4]              # 两个根，按 sort_order
    assert [c["id"] for c in tree[0]["children"]] == [2, 3]
    assert tree[1]["children"] == []


def test_build_tree_sorts_by_sort_order_then_id():
    rows = [_f(2, None, "b", 5), _f(1, None, "a", 5), _f(3, None, "c", 1)]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [3, 1, 2]           # sort_order 升序，同序按 id


def test_build_tree_orphan_parent_treated_as_root():
    # parent_id 指向不存在的父（脏数据）→ 当根处理，不丢失
    rows = [_f(9, 99, "孤儿", 0)]
    tree = kfs.build_folder_tree(rows)
    assert [n["id"] for n in tree] == [9]
