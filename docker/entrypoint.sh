set -e

echo "[INFO] Booting Test Platform..."

# 可选：启动 Appium
if [ "$START_APPIUM" = "true" ]; then
  echo "[INFO] Starting Appium..."
  # 允许自动下载 chromedriver
  appium --allow-insecure chromedriver_autodownload --log-level info &
  sleep 5
fi

# 可选：启动 Locust（若你想单独用这个容器承载 Locust，也可以在 docker-compose 单独服务）
if [ "$START_LOCUST" = "true" ]; then
  echo "[INFO] Starting Locust..."
  locust -f src/core/load_test/locust_tasks.py --host="${LOCUST_HOST:-http://localhost:8000}" &
  sleep 3
fi

# 可选：启动 mitmproxy
if [ "$START_MITMPROXY" = "true" ]; then
  echo "[INFO] Starting mitmproxy..."
  mitmproxy --listen-port 8080 &
  sleep 3
fi

echo "[INFO] Running main: python src/main.py $*"
exec python src/main.py "$@"