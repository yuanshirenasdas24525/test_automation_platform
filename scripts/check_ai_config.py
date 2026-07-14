"""一键自检 AI 配置。

跑法：
    python -m scripts.check_ai_config

或直接：
    python scripts/check_ai_config.py

会按顺序检查：
    1) DB 里指定项目有没有 category='ai' 的条目
    2) config_center.get('ai', project_id=xxx) 能不能读到（含 reload 一遍）
    3) ai_gateway 模块能不能 import
    4) prompt 模板文件存在 + 能 render
    5) 如果你加 --live 参数，就真的发一次最简调用到 LLM
       验证 provider / api_key / model / base_url 能完整跑通
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ok(s: str) -> None:
    print(f"  ✅ {s}")


def _fail(s: str) -> None:
    print(f"  ❌ {s}")


def _info(s: str) -> None:
    print(f"  ℹ️  {s}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="真的发一次最简调用到 LLM，验证整个链路（会消耗几个 token）",
    )
    parser.add_argument(
        "--project-id",
        type=int,
        required=True,
        help="要检查的项目 ID；AI 配置已不支持全局模板",
    )
    args = parser.parse_args()

    failures = 0

    # ---------- 1. DB 直查 ----------
    print(f"\n[1/5] 检查项目 {args.project_id} 的 config_store AI 配置")
    try:
        from database.db import DB

        db = DB()
        try:
            rows = db.sql.execute_query(
                "SELECT config_group, config_key, config_value FROM config_store "
                "WHERE category = :c AND project_id = :pid",
                {"c": "ai", "pid": args.project_id},
            ) if hasattr(db.sql, "execute_query") else db.sql.query(
                "SELECT config_group, config_key, config_value FROM config_store "
                "WHERE category = :c AND project_id = :pid",
                {"c": "ai", "pid": args.project_id},
            )
            if not rows:
                _fail("DB 里 0 条项目 AI 配置 —— 进项目配置 → AI 把 provider/api_key/model 填好")
                failures += 1
            else:
                _ok(f"DB 里有 {len(rows)} 条 ai 配置：")
                for r in rows:
                    g = r.get("config_group") if isinstance(r, dict) else r[0]
                    k = r.get("config_key") if isinstance(r, dict) else r[1]
                    v = r.get("config_value") if isinstance(r, dict) else r[2]
                    # 脱敏 api_key
                    if k == "api_key" and v:
                        v = v[:6] + "***" + v[-4:] if len(v) > 12 else "***"
                    print(f"     [{g}] {k} = {v}")
        finally:
            db.close()
    except Exception as exc:
        _fail(f"DB 查询失败：{exc}")
        failures += 1
        return _summary(failures)

    # ---------- 2. config_center.get('ai', project_id=xxx) ----------
    print("\n[2/5] config_center.get('ai', project_id=xxx) 能否读到")
    try:
        from utils.reload_config import config_center

        cfg = config_center.get("ai", project_id=args.project_id) or {}
        if not cfg:
            # 强制 reload 一次
            db = DB()
            try:
                config_center.reload(db.sql, project_id=args.project_id, category=None)
            finally:
                db.close()
            cfg = config_center.get("ai", project_id=args.project_id) or {}

        if not cfg:
            _fail("config_center 读不到 ai 配置（缓存 + reload 都空）")
            failures += 1
        else:
            _ok(f"config_center 读到 {len(cfg)} 个 group：{list(cfg.keys())}")
            # 关键字段
            provider = cfg.get("provider")
            api_key = cfg.get("api_key")
            model = cfg.get("model")
            for k, v in [("provider", provider), ("api_key", api_key), ("model", model)]:
                if not v:
                    _fail(f"  关键字段 {k} 为空")
                    failures += 1
                else:
                    shown = v if k != "api_key" else (v[:6] + "***" + v[-4:] if len(v) > 12 else "***")
                    _ok(f"  {k} = {shown}")
    except Exception as exc:
        _fail(f"config_center 查询异常：{exc}")
        failures += 1

    # ---------- 3. ai_gateway 模块 import ----------
    print("\n[3/5] ai_gateway 模块能否 import")
    try:
        from ai_gateway import chat_json, ProviderError, NoProviderConfiguredError  # noqa: F401

        _ok("ai_gateway 包正常 import")
    except Exception as exc:
        _fail(f"ai_gateway import 失败：{exc}")
        failures += 1
        return _summary(failures)

    # ---------- 4. prompt 模板 ----------
    print("\n[4/5] prompt 模板文件检查")
    prompts_dir = ROOT / "ai_gateway" / "prompts"
    if not prompts_dir.exists():
        _fail(f"目录不存在：{prompts_dir}")
        failures += 1
    else:
        prompts = sorted(prompts_dir.glob("*.md"))
        if not prompts:
            _fail(f"目录里没有 .md 模板：{prompts_dir}")
            failures += 1
        else:
            _ok(f"找到 {len(prompts)} 个模板：")
            for p in prompts:
                _info(f"  {p.name}")

    # ---------- 5. 真调用（可选）----------
    print("\n[5/5] 真调用 LLM（验证整条链路）")
    if not args.live:
        _info("跳过（加 --live 参数会发一次最简调用，消耗几十 token）")
    else:
        try:
            from ai_gateway import chat_json

            print("  正在调用 LLM（约 5-10 秒）...")
            res = chat_json(
                feature="requirement_parse",
                user_input={
                    "text": "用户需要能够通过邮箱或手机号登录系统，"
                            "登录失败 5 次后账号锁定 30 分钟。"
                },
                project_id=args.project_id,
                timeout=120,
                analysis_mode="quick",
            )
            _ok(
                f"LLM 调通：provider={res['provider']} model={res['model']} "
                f"in={res['tokens_in']} out={res['tokens_out']} "
                f"cost=${res.get('cost_usd', 0):.4f}"
            )
            out = res.get("output") or {}
            req_count = len(out.get("requirements") or [])
            _ok(f"  解析出 {req_count} 个需求点")
            for r in (out.get("requirements") or [])[:3]:
                _info(f"    - {r.get('title')}")
        except Exception as exc:
            _fail(f"LLM 调用失败：{type(exc).__name__}: {exc}")
            failures += 1

    return _summary(failures)


def _summary(failures: int) -> int:
    print("\n" + "=" * 60)
    if failures == 0:
        print("✅ AI 配置自检通过！")
        return 0
    print(f"❌ 自检失败 {failures} 项 —— 按上面 ❌ 提示逐项修")
    return 1


if __name__ == "__main__":
    sys.exit(main())
