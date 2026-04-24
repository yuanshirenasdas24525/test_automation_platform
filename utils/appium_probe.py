"""对 Appium Server 做可用性探测的助手。

抽出来单放是为了避免 `tasks/probe_devices.py` 反向 import `server.api.system`，
那会拖 FastAPI / APIRouter 进 Celery worker 的进程空间。

探测流程：
  1. 先 TCP connect：连不上就直接 down（"no TCP"），这类问题是真·没人监听。
  2. 再依次试 Appium 2 默认 (`/status`) 和 Appium 1 (`/wd/hub/status`)。
     我们走 `http.client.HTTPConnection` 而不是 urllib，因为：
       - 能完整控制 header，Appium 2 中间件对裸 urllib 偶尔会 drop 连接；
       - 便于区分 "TCP 通但 HTTP 没响应"（RemoteDisconnected）和 "HTTP 4xx/5xx"。
  3. 带完整头部（User-Agent / Accept），避免被 middleware 误判成坏爬虫。

返回 `(ok, detail)`；detail 在失败时说明具体卡在哪一步，方便前端排障。
"""
from __future__ import annotations

import http.client
import socket


def probe_appium(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    """对一台 Appium server 做可用性探测。"""
    # 1) 先 TCP 探一下。TCP 都连不上，就别浪费 HTTP 的 timeout 了。
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except Exception as exc:  # noqa: BLE001
        return False, f"TCP 连接失败: {exc}"

    # 2) HTTP 探测。尝试 Appium 2、Appium 1 两种 base path。
    last_err = "未知错误"
    for path in ("/status", "/wd/hub/status"):
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    # Appium 2 的 Express middleware 对没 User-Agent 的连接比较敏感。
                    "User-Agent": "AutoTestPlatform-Healthcheck/1.0",
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )
            resp = conn.getresponse()
            body = resp.read(256)  # 只读前 256 字节做 sanity check
            if 200 <= resp.status < 300:
                # 标准 Appium 响应里必有 "value" 字段；没有也不强挂，HTTP 200 就算活着
                return True, f"HTTP {resp.status} {path}"
            last_err = f"HTTP {resp.status} {path}（body={body[:80]!r}）"
        except http.client.RemoteDisconnected:
            # TCP 通但服务端没发 response 就断 —— 通常是端口被占用，或 Appium 在启动/崩溃中
            last_err = (
                f"{path}: 连接被对端提前关闭（Remote end closed），"
                "端口可能被占或不是 Appium"
            )
        except (http.client.HTTPException, ConnectionError, OSError, socket.timeout) as exc:
            last_err = f"{path}: {type(exc).__name__}: {exc}"
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    return False, last_err
