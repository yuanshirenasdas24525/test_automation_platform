"""脚本库独立运行时。

该包只能定义脚本执行协议，不得依赖 ``server``、``database`` 或 ``runners``。
项目脚本由父进程通过 JSON stdin 传入，并在独立 Python 进程中执行。
"""
