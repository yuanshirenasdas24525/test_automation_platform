#!/bin/bash
set -e

echo "[INFO] Booting Test Platform..."

RESULT_DIR="/app/data/reports/allure-results"
REPORT_DIR="/app/data/reports/allure-report"
# === Function: 等待 Appium 服务启动 ===
wait_for_appium() {
  local host="${APPIUM_HOST:-appium_server}"
  local port="${APPIUM_PORT:-4723}"
  local max_retries=30
  local retry=0
  echo "[INFO] 正在等待 Appium 服务器 ${host}:${port} ..."
  while ! nc -z "${host}" "${port}" >/dev/null 2>&1; do
    retry=$((retry+1))
    if [ $retry -ge $max_retries ]; then
      echo "[ERROR] 等待 Appium 的超时 (${host}:${port})"
      exit 1
    fi
    echo "[INFO] Appium 尚未准备就绪，重试 ${retry}/${max_retries}..."
    sleep 2
  done
  echo "[INFO] Appium 服务器已启动并可访问!"
}
# === 检测参数 ===
if [ $# -eq 0 ]; then
  echo "[INFO] No command provided, starting bash for debugging..."
  exec /bin/bash
fi
# === 清理旧报告 ===
echo "[INFO] Cleaning old Allure results..."
rm -rf ${RESULT_DIR:?}/* || true
# === 历史报告迁移 ===
if [ -d "$REPORT_DIR/history" ]; then
  echo "[INFO] Found history data, migrating..."
  mkdir -p ${RESULT_DIR}/history
  cp -r ${REPORT_DIR}/history/* ${RESULT_DIR}/history/ || true
fi
# === 检查是否是 UI 测试 ===
#if [[ " $@ " =~ " -t mobile" ]] || [[ " $@ " =~ " --type mobile" ]]; then
#  wait_for_appium
#fi
# === 执行主程序 ===
echo "[INFO] Running main.py with args: $@"
python src/main.py "$@" --alluredir="${RESULT_DIR}"
# === 生成报告 ===
echo "[INFO] Generating Allure report..."
allure generate ${RESULT_DIR} -o ${REPORT_DIR} --clean

if [ "$CI" = "true" ]; then
  echo "[INFO] CI mode: Report generated at ${REPORT_DIR}"
else
  echo "[INFO] Local mode: Starting Allure server..."
  allure serve ${RESULT_DIR}
fi