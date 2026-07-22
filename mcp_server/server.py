"""平台 MCP server 入口 —— M1 工具集（stdio）。

工具清单（对照 docs/方案-MCP-server草案.md 第三节；M1 + M2 写工具）：
  - list_projects        → GET  /api/projects/list
  - list_modules         → GET  /api/modules
  - list_cases           → GET  /api/content/{project_id}（自动化）/ GET /api/functional_cases（功能）
  - run_tests            → POST /api/run_test
  - get_report           → GET  /api/reports/{id}（默认只回失败/错误步骤，做字段裁剪）
  - get_report_failures  → GET  /api/reports/{id}/failures（失败明细最小集）
  - get_coverage         → GET  /api/requirements/coverage
  - list_ai_models       → GET  /api/ai-models
  - diagnose_report      → POST /api/functional_cases/ai_diagnose_report（异步，返回 ai_run_id）
  - get_ai_run           → GET  /api/ai/runs/{id}
  - apply_report_fixes   → POST /api/functional_cases/ai_report_fix/apply（写用例，需用户确认）

约束（草案第二节"边界"）：只读多、写少；不暴露删除 / 配置 / 用户管理。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.http_client import PlatformClient

mcp = FastMCP(
    "test-automation-platform",
    instructions=(
        "自动化测试平台的验证工具集。典型回归回路：\n"
        "list_projects 拿 project_id → list_modules / list_cases 找用例 →"
        " run_tests 触发执行（返回 report_id）→ 轮询 get_report 直到 status 不是 running →"
        " 失败时读 get_report 里的失败步骤明细（含请求/断言信息）。\n"
        "get_coverage 可查需求-用例覆盖缺口。注意：功能用例（functional）只能人工执行，"
        "run_tests 不接受 category=functional。"
    ),
)

_client: PlatformClient | None = None


def _api() -> PlatformClient:
    """懒初始化：MCP 宿主起进程时未必配好环境变量，首次调用工具时再连。"""
    global _client
    if _client is None:
        _client = PlatformClient()
    return _client


@mcp.tool()
def list_projects() -> list[dict[str, Any]]:
    """列出平台的所有项目（id / name 等），后续工具都需要 project_id。"""
    data = _api().get_data("/api/projects/list")
    return data or []


@mcp.tool()
def list_modules(project_id: int) -> list[dict[str, Any]]:
    """列出项目下所有模块的扁平列表（id / name / parent_id），parent_id 为空表示顶层模块。"""
    data = _api().get_data("/api/modules", params={"project_id": project_id})
    return data or []


@mcp.tool()
def list_cases(
    project_id: int,
    module_id: int | None = None,
    case_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Any:
    """列出用例。

    - case_type='functional'：功能用例，支持 project_id 全量 + 分页（page / page_size）；
    - 其它（api / web / android / ios / mixed 或不传）：自动化用例，按模块层级返回 ——
      不传 module_id 时只返回顶层模块列表，传 module_id 返回该模块下的子模块 + 用例，
      需要逐层下钻（配合 list_modules 直接定位模块更快）。
    """
    if case_type == "functional":
        body = _api().request(
            "GET",
            "/api/functional_cases",
            params={
                "project_id": project_id,
                "module_id": module_id,
                "page": page,
                "page_size": page_size,
            },
        )
        return {"data": body.get("data"), "total": body.get("total")}

    data = _api().get_data(
        f"/api/content/{project_id}",
        params={"parent_id": module_id, "case_type": case_type},
    )
    # 裁剪：agent 决策只需要 id / 名称 / 类型 / 归属，不回传 sort_order 之类的 UI 字段
    items = data or []
    return [
        {k: item.get(k) for k in ("id", "name", "type", "case_type", "module_id", "parent_id")}
        for item in items
    ]


@mcp.tool()
def run_tests(
    project_id: int,
    category: str,
    case_ids: list[int] | None = None,
    module_id: int | None = None,
    device_id: int | None = None,
) -> dict[str, Any]:
    """触发一次自动化回归执行，返回 report_id（用 get_report 轮询结果）。

    - category：api / web / android / ios（functional 不支持自动化执行）；
    - case_ids：指定用例 id 列表；不传则按 project + module + category 圈用例；
    - device_id：android/ios 用例可指定一台 idle 设备。
    执行是异步的：本工具立即返回，用 get_report 轮询 status 直到不是 running
    （api 用例通常秒级~分钟级，web/app 用例更久，轮询间隔建议 5-10 秒）。
    """
    body = _api().request(
        "POST",
        "/api/run_test",
        json_body={
            "project": project_id,
            "category": category,
            "case_ids": case_ids,
            "module": module_id,
            "device_id": device_id,
        },
    )
    return {
        "report_id": body.get("report_id"),
        "task_id": body.get("task_id"),
        "case_number": body.get("case_number"),
        "message": body.get("message"),
    }


@mcp.tool()
def get_report(report_id: int, include_passed_steps: bool = False) -> dict[str, Any]:
    """读一份执行报告：汇总（状态 / 通过率 / 时长）+ 步骤明细。

    status=running 表示还在跑，稍后再查。默认只返回失败(failed)/错误(error)步骤
    （含 action / target / status_code / error_message，是定位失败原因的关键输入）；
    include_passed_steps=True 时返回全部步骤。
    """
    data = _api().get_data(f"/api/reports/{report_id}") or {}
    steps = data.pop("steps", [])
    if not include_passed_steps:
        steps = [s for s in steps if (s.get("status") or "").lower() not in ("passed", "skipped")]
    # 步骤裁剪：去掉 report_id / create_time 等对决策无用的字段
    step_keys = (
        "case_id", "step_name", "step_type", "action", "target",
        "status", "status_code", "duration", "error_message",
    )
    data["steps"] = [{k: s.get(k) for k in step_keys} for s in steps]
    # 报告级裁剪：allure_url 保留（人想看完整报告时用），summary 可能较长但有用
    data.pop("create_time", None)
    return data


@mcp.tool()
def get_report_failures(report_id: int) -> dict[str, Any]:
    """读一份报告的失败明细最小集（按用例分组，含 action/target/状态码/错误信息）。

    比 get_report 更精简，是"分析失败原因 → 修代码或修用例"的首选输入。
    """
    return _api().get_data(f"/api/reports/{report_id}/failures") or {}


@mcp.tool()
def triage_report_failures(report_id: int) -> dict[str, Any]:
    """对报告的失败用例做确定性分诊（不调 LLM、零成本），给出每条的归因与建议。

    分类：用例问题 / 接口问题 / 环境或其他 / 待定。每条带 evidence 说明依据，
    部分还带 fix_hint（如算出的正确 JSONPath）。
    **排查失败原因时优先调它**，比直接读 get_report_failures 的原始报错更省事；
    只有分类为"待定"的才需要进一步用 diagnose_report 走 AI 分析。
    """
    return _api().get_data(f"/api/reports/{report_id}/triage") or {}


@mcp.tool()
def get_coverage(project_id: int) -> dict[str, Any]:
    """查需求-用例覆盖率：按需求 / 按模块两个维度，返回覆盖缺口（该补用例的地方）。"""
    return _api().get_data("/api/requirements/coverage", params={"project_id": project_id}) or {}


@mcp.tool()
def list_ai_models(project_id: int) -> Any:
    """列出该项目配置的 AI 模型（diagnose_report 的 model_name 参数从这里选）。"""
    return _api().get_data("/api/ai-models", params={"project_id": project_id}) or []


@mcp.tool()
def diagnose_report(report_id: int, model_name: str) -> dict[str, Any]:
    """对一份报告的失败用例发起 AI 诊断（异步），返回 ai_run_id。

    用 get_ai_run 轮询到 status=success 后，诊断结果在 output_payload.items；
    确认要应用修复时再调 apply_report_fixes。model_name 用 list_ai_models 里的名称。
    """
    body = _api().request(
        "POST",
        "/api/functional_cases/ai_diagnose_report",
        json_body={"report_id": report_id, "model_name": model_name},
    )
    return body.get("data") or body


@mcp.tool()
def get_ai_run(run_id: int) -> dict[str, Any]:
    """查一个 AI 任务（诊断/生成等）的状态与结果。status=success 时读 output_payload。"""
    return _api().get_data(f"/api/ai/runs/{run_id}") or {}


@mcp.tool()
def apply_report_fixes(ai_run_id: int, verify: bool = True, max_rounds: int = 1) -> dict[str, Any]:
    """【写操作，会修改用例】把 AI 诊断结果应用到用例，并触发闭环验证。

    平台侧自带三重保护：应用前 preflight 预检（坏修复直接拦掉不落库）、
    每用例独立编辑事件可精准回滚、verify=True 时自动重跑原报告且"绿变红"自动回滚。
    仍属于会改动用例库的操作 —— 调用前必须先向用户确认，未经确认不要调用。
    max_rounds>1 表示仍失败的用例会带新证据自动再诊断再修。
    """
    body = _api().request(
        "POST",
        "/api/functional_cases/ai_report_fix/apply",
        json_body={"ai_run_id": ai_run_id, "verify": verify, "max_rounds": max_rounds},
    )
    return body.get("data") or body


if __name__ == "__main__":
    mcp.run()
