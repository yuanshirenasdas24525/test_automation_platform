"""从一份测试报告里学习"真实响应结构约定",写入记忆层(project_contexts.api_contract)。

背景:AI 生成接口用例时靠猜 JSONPath(如 $.access_token),但真实响应是带信封的
     ({status, data:{access_token}})→ 首次运行大面积失败。真相其实藏在报告的真实响应里。
     本脚本把真实响应样本喂给 LLM,提炼成"响应约定"事实回流记忆层,
     下次生成 interface 用例时经 PROJECT_CONTEXT 注入 → 直接写对路径。

这是"回流事实"闭环的一环:首次生成猜 → 运行暴露真相 → 真相入记忆 → 下次生成对。

用法(项目根目录):
    venv/bin/python scripts/learn_response_convention.py <report_task_id> <model_name>
例:
    venv/bin/python scripts/learn_response_convention.py 20260712025715_240f0f27 deepseek
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

def _collect_samples(task_id: str, limit: int = 12) -> list[dict]:
    """从 Allure 报告的 test-cases 里抽真实 请求+响应 样本。"""
    base = _ROOT / "data" / "reports" / task_id / "data"
    tc_dir = base / "test-cases"
    att_dir = base / "attachments"
    if not tc_dir.exists():
        raise SystemExit(f"找不到报告数据目录: {tc_dir}")

    def _load_att(name_hint: str, atts: list[dict]) -> dict | None:
        for a in atts:
            if name_hint in (a.get("name") or ""):
                p = att_dir / (a.get("source") or "")
                if p.exists():
                    try:
                        return json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        return None
        return None

    def _all_atts(step) -> list[dict]:
        """一个步骤(含所有后代子步)的全部 attachments 展平。"""
        out = list(step.get("attachments", []) or [])
        for c in step.get("steps", []) or []:
            out.extend(_all_atts(c))
        return out

    samples: list[dict] = []
    for f in sorted(tc_dir.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        cname = t.get("name", "")[:60]
        # 每个顶层 http_request 步 = 一个 请求/响应 样本;Request 与 Response
        # 挂在该步的不同子步上,所以在整步范围内聚合
        for step in t.get("testStage", {}).get("steps", []) or []:
            atts = _all_atts(step)
            req = _load_att("Request", atts)
            resp = _load_att("Response", atts)
            if req and resp:
                samples.append({
                    "name": f"{cname} / {step.get('name','')[:40]}",
                    "request": req,
                    "response": resp,
                })
            if len(samples) >= limit:
                break
        if len(samples) >= limit:
            break
    return samples[:limit]


def main(task_id: str, model_name: str, project_id_arg: int | None = None) -> None:
    from database.db import DB
    from database.models import TestReport
    from server.services.ai_model_service import get_ai_model

    samples = _collect_samples(task_id)
    if not samples:
        raise SystemExit("没抽到 请求+响应 样本(报告可能没有 http 用例)")
    print(f"抽取真实样本 {len(samples)} 条")

    db = DB()
    try:
        project_id = project_id_arg
        if project_id is None:
            # task_id 存在 allure_url 里,按它反查报告拿 project_id
            report = (
                db.session.query(TestReport)
                .filter(TestReport.allure_url.like(f"%{task_id}%"))
                .first()
            )
            if report is None or not report.project_id:
                raise SystemExit(
                    f"按 task_id 反查报告失败,请显式传 project_id:\n"
                    f"  venv/bin/python scripts/learn_response_convention.py {task_id} {model_name} <project_id>"
                )
            project_id = report.project_id
        print(f"project_id = {project_id}")

        cfg = get_ai_model(db.session, model_name)
        if cfg is None or not cfg.enabled:
            raise SystemExit(f"模型 {model_name!r} 未配置/未启用")

        # 复用服务层的提炼+入库核心（与报告后自动学同一套逻辑/prompt）
        from server.services.response_convention import distill_and_save

        created = distill_and_save(
            db.session, project_id=project_id, samples=samples, cfg=cfg,
            source_file=f"report:{task_id}",
        )
        db.commit()
        if not created:
            print("模型没提炼出约定(或与已有条目重复)")
            return
        print(f"\n✅ 写入记忆层 {len(created)} 条(重复自动跳过)")
        print("下一步:重新生成该模块的接口用例,extract/assertion 会按真实结构写。")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: venv/bin/python scripts/learn_response_convention.py <report_task_id> <model_name> [project_id]")
        sys.exit(1)
    pid = int(sys.argv[3]) if len(sys.argv) > 3 else None
    main(sys.argv[1], sys.argv[2], pid)
