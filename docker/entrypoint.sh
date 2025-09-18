#!/bin/bash
set -e

echo "[INFO] Booting Test Platform..."

RESULT_DIR="/app/data/reports/allure-results"
REPORT_DIR="/app/data/reports/allure-report"

# 如果没传任何参数，就进入交互模式
if [ $# -eq 0 ]; then
  echo "[INFO] No command provided, starting bash for debugging..."
  exec /bin/bash
fi

# 清理旧的 allure 结果
echo "[INFO] Cleaning old Allure results..."
rm -rf ${RESULT_DIR:?}/*

# 如果有历史数据则迁移
if [ -d "$REPORT_DIR/history" ]; then
  echo "[INFO] Found history data, migrating..."
  mkdir -p ${RESULT_DIR}/history
  cp -r ${REPORT_DIR}/history/* ${RESULT_DIR}/history/ || true
fi

# 执行主程序
echo "[INFO] Running main.py with args: $@"
python src/main.py "$@" --alluredir="${RESULT_DIR}"

# 生成 Allure 报告
echo "[INFO] Generating Allure report..."
allure generate ${RESULT_DIR} -o ${REPORT_DIR} --clean

if [ "$CI" = "true" ]; then
  echo "[INFO] CI mode: Report generated at ${REPORT_DIR}"
else
  echo "[INFO] Local mode: Starting Allure server..."
  allure serve ${RESULT_DIR}
fi