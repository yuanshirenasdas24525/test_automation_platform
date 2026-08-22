"""功能用例大纲「接地过滤」（B 层第一道，纯函数）。

背景：功能用例的 outline→batch 生成链路里，`functional_case_outline` 在"穷尽覆盖"
下会把每个维度铺满，并**捏造系统根本没有的能力**（第三方登录 / GDPR / 自动伸缩 /
技术债评估…），这些点结构上合法，会一路漏到用例库变成噪音。

本模块只做**关键词黑名单快筛 + 项目白名单豁免**（不碰 DB、不调 LLM）：
  - 命中黑名单词 → 判为越界（out_of_scope），在大纲阶段就剔除；
  - 若该词在项目上下文/需求文本里**确实出现过**（说明本项目真的有这能力），豁免放行；
  - 拿不准的（如"忘记密码""双因素"这类可能有也可能没有的功能）不在这里硬删，
    留给 LLM 相关性二判（见 functional_cases._run_functional_scope_judge）兜。

调用方：server/api/functional_cases.py::ai_generate_outline（mode=functional）。
"""
from __future__ import annotations

import re

# 分类维护便于 review / 后续按项目关闭某一类；值都用小写、去空格后比对。
# 只放"对本类 Web 业务功能用例几乎必然越界"的词；模棱两可的交给 LLM 二判。
OUT_OF_SCOPE_HINTS: dict[str, tuple[str, ...]] = {
    # 外部认证集成协议——不在则不写
    "auth_integration": (
        "oauth", "saml", "ldap", "sso", "单点登录", "第三方登录", "第三方认证",
        "微信登录", "github登录", "钉钉登录", "cas认证",
    ),
    # 运维 / 基建 / 部署架构——不是功能用例，属于部署与运维测试
    "infra": (
        "负载均衡", "自动伸缩", "弹性伸缩", "自动扩容", "自动缩容", "多实例部署",
        "实例宕机", "节点宕机", "服务降级", "熔断", "ddos", "waf", "容灾",
        "高可用", "灰度发布", "蓝绿部署", "无感知切换", "自动伸缩策略",
    ),
    # 合规 / 数据治理——本类内部测试平台不适用
    "compliance": (
        "gdpr", "ccpa", "数据保留策略", "数据备份恢复", "数据备份与恢复",
        "隐私合规", "等保合规",
    ),
    # 过程 / 审计条目——根本不是"可执行测试用例"
    "process_audit": (
        "文档完整性", "接口文档完整", "代码质量", "技术债", "技术负债",
        "开源许可证", "许可证合规", "向后兼容性检查", "弃用策略", "可维护性",
        "可移植性", "测试覆盖率检查", "代码坏味道",
    ),
    # 对 Python/JS Web 应用没有意义的底层攻击面
    "irrelevant_lowlevel": (
        "缓冲区溢出", "buffer overflow", "内存溢出攻击", "栈溢出",
    ),
}

# 展平，加载时算一次
_FLAT_HINTS: tuple[tuple[str, str], ...] = tuple(
    (kw, category)
    for category, kws in OUT_OF_SCOPE_HINTS.items()
    for kw in kws
)


def _compact(text: str) -> str:
    """小写并去掉所有空白，便于"负 载 均 衡"这类被空格拆开的命中。"""
    return re.sub(r"\s+", "", str(text or "").lower())


_CATEGORY_PREFIX_RE = re.compile(
    r"^[【\[（(]?(正向|正常|异常|边界|安全|权限|鉴权|越权|参数校验|响应校验|"
    r"兼容|性能|兼容/性能|界面与交互|跨模块|场景|关联|其它|其他)[】\]）)]?[:：]?"
)


def _norm_title(s: str) -> str:
    """标题归一化：去类别前缀、标点、空白，转小写，供近重复比对。

    与 draft_validation._norm_title 同源思路，这里多兜一个 mode 用的类别词表。
    """
    t = str(s or "")
    t = _CATEGORY_PREFIX_RE.sub("", t)
    t = re.sub(r"[\s\-_、，,。.；;：:（）()【】\[\]/]+", "", t)
    return t.lower()


def dedup_points(
    points: list[dict],
    threshold: float = 0.9,
) -> tuple[list[dict], list[dict]]:
    """去掉**批内近重复**测试点（词法相似度）。返回 (保留, 判为重复)。

    outline 路径原本没有测试点级去重，模型偶尔会同时产出"详细版 + 泛化版"两条
    几乎一样的点（如"请求体格式错误(非JSON)返回422" vs "请求体格式错误(非JSON)"）。
    用 difflib 序列相似度，不依赖 embedding；只在批内去重，不动已入库用例。
    """
    import difflib

    def _is_dup(nt: str, s: str) -> bool:
        if nt == s:
            return True
        # 子串包含：一条是另一条的"加后缀/前缀"版（如 …非JSON 与 …非JSON返回422）。
        # 要求较短一条足够长（≥10），避免"登录成功"这类短词误伤。
        short, long = (nt, s) if len(nt) <= len(s) else (s, nt)
        if len(short) >= 10 and short in long:
            return True
        return difflib.SequenceMatcher(None, nt, s).ratio() >= threshold

    kept: list[dict] = []
    dropped: list[dict] = []
    seen: list[str] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        nt = _norm_title(p.get("title") or "")
        if not nt:
            kept.append(p)
            continue
        if any(_is_dup(nt, s) for s in seen):
            dropped.append(p)
        else:
            kept.append(p)
            seen.append(nt)
    return kept, dropped


def classify_point_scope(title: str, context_compact: str) -> tuple[bool, str]:
    """判定单个测试点是否越界。

    返回 (is_out_of_scope, reason)。reason 形如 "out_of_scope:infra:负载均衡"。
    context_compact 由 build_context_compact() 预处理，避免每条点重复归一化。
    """
    compact = _compact(title)
    if not compact:
        return False, ""
    for kw, category in _FLAT_HINTS:
        if kw in compact:
            # 项目白名单豁免：该能力词在项目上下文/需求里确有出现 → 本项目真的有，放行
            if kw in context_compact:
                continue
            return True, f"out_of_scope:{category}:{kw}"
    return False, ""


def build_context_compact(*context_texts: str) -> str:
    """把项目上下文 / 需求文本拼成一份归一化白名单底本。"""
    return _compact("\n".join(t for t in context_texts if t))


def filter_out_of_scope_points(
    points: list[dict],
    *context_texts: str,
) -> tuple[list[dict], list[dict]]:
    """剔除越界测试点。返回 (保留, 剔除)；剔除项带 `_scope_reason` 便于日志/统计。

    - points：[{"title", "category"}, ...]
    - context_texts：项目上下文、需求文本等，用于白名单豁免
    """
    context_compact = build_context_compact(*context_texts)
    kept: list[dict] = []
    dropped: list[dict] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        is_out, reason = classify_point_scope(str(p.get("title") or ""), context_compact)
        if is_out:
            dropped.append({**p, "_scope_reason": reason})
        else:
            kept.append(p)
    return kept, dropped
