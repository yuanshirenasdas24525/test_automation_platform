# AI 接口用例生成质量门禁

接口用例生成采用“AI 规划场景、平台按 OpenAPI 编译”的模式。生成、在线探测和正式
执行必须复用 `compiled_case`，禁止前端或探测器再次拼装另一份 HTTP 步骤。

## 生成链路

1. 大纲阶段解析 OpenAPI `$ref`、参数位置、required、enum、security、请求体和响应 schema，
   生成带 SHA-256 hash 的紧凑契约。
2. 批次阶段按 operationId/path 精确提供相关契约；AI 输出场景和测试数据意图。
3. `server/services/api_case_contract.py` 生成唯一执行载荷，确定状态码、基础断言和请求位置。
4. 硬门禁检查契约、认证、变量、必填字段、枚举、JSONPath 和清理请求。未通过的草稿
   `needs_fix=true`，默认不选中，且 `POST /api/test_cases` 会拒绝入库。
5. 在线探测只运行静态校验通过的单步安全请求。成功写操作没有清理步骤时跳过；实际状态码
   与契约不一致时阻断，禁止把错误响应学习成正确断言。

## 追踪字段与指标

新生成的接口用例写入：

- `source=ai_interface`
- `generation_metadata.generation_run_id/model/provider/prompt_version`
- `generation_metadata.contract_hash/compiler_version`
- `generation_metadata.preflight/probe`

质量查询：

```text
GET /api/functional_cases/ai_generation_quality?project_id=<项目ID>
```

接口按 `source=ai_interface` 单独计算契约门禁通过率、探测通过数、首次真实执行通过率、
最新执行通过率及 Prompt 版本分组，避免人工用例混入后稀释指标。

## 运行与回归

```bash
alembic upgrade head
pytest -q tests/test_api_case_contract.py tests/test_http_request_partition.py
```

本地生成若需要读取 `127.0.0.1` 或内网 Swagger，请只在可信测试环境显式设置
`DOC_FETCH_ALLOW_PRIVATE=1`；生产环境保持关闭。
