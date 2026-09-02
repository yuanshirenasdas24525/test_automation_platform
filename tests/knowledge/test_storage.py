"""知识库文件存储 纯函数单测（不落盘）。"""
from utils import knowledge_storage as st


def test_safe_ext_whitelist():
    assert st.safe_ext("a.PDF") == ".pdf"
    assert st.safe_ext("b.docx") == ".docx"
    assert st.safe_ext("c.png") == ".png"
    assert st.safe_ext("evil.exe") == ""
    assert st.safe_ext("noext") == ""


def test_is_allowed():
    assert st.is_allowed("x.xlsx") is True
    assert st.is_allowed("x.sh") is False


def test_within_size():
    assert st.within_size(1) is True
    assert st.within_size(st.MAX_SIZE_BYTES) is True
    assert st.within_size(0) is False
    assert st.within_size(st.MAX_SIZE_BYTES + 1) is False
