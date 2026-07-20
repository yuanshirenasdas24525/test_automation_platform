"""平台 MCP server（M1 PoC）—— 把平台的验证能力暴露给 coding agent。

设计草案见 docs/方案-MCP-server草案.md。要点：
  - HTTP 客户端式薄封装：只调平台 REST API，不 import server.services、不开 DB session；
  - 只暴露"验证回路"需要的最小工具集：列项目/模块/用例、触发执行、读报告、查覆盖率；
  - 写操作仅 run_tests（触发执行）；用例入库、修复应用等留给 M2+（需 dry-run + 人工评审）。

启动（stdio 模式，由 MCP 宿主拉起）：
    python -m mcp_server.server
"""
