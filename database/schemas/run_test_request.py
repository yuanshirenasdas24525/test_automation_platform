from __future__ import annotations

import pydantic


class RunTestRequest(pydantic.BaseModel):
    project: int
    module: int | None = None
    category: str | None = None
    case: int | None = None
    # API 工作台批量运行：所有 id 合并到同一份 TestReport，测试记录按该报告聚合。
    case_ids: list[int] | None = None
    # 指定一台设备跑 app/android/ios 用例。传了就忽略 env.device_pool/platform 过滤，
    # 直接 `DevicePool.acquire_by_id(device_id)`；设备必须 idle，否则 409。
    # 只对 category 在 {app, android, ios} 时有意义；其它类型忽略。
    device_id: int | None = None
    # 「AI 自愈运行」：执行本身与普通运行完全一致，跑完后额外做
    # 分诊 → 应用可算出的修复 → 重跑验证（见 tasks/ai_heal_task.py）。
    # ai_model 为空时只做零成本的规则自愈；给了模型才会调 LLM 深度诊断。
    ai_heal: bool = False
    ai_model: str | None = None
