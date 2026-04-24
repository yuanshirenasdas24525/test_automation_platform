# 故意保留为空。
#
# 加这个 __init__.py 是为了避免 pytest 把 `tests/` 当成 sys.path rootpath。
#
# 背景：之前 tests/ 没有 __init__.py，但 tests/runners/ 有 —— pytest 在收集
# tests/service_run_executor.py 时，会把 tests/ 自身插进 sys.path（因为它是
# 第一个"无 __init__.py"的祖先目录）。这会导致 `import runners` 解析到
# tests/runners/ 而不是顶层 runners/，引发 `No module named runners.case_executor`。
#
# 加上本文件后，pytest 会一直往上找到项目根作为 rootpath，tests.runners 变成
# 子包，不再遮蔽顶层 runners 包。
