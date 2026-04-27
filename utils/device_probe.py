"""设备级心跳探测：除了 Appium server 本身的 /status，还要确认 udid 真的"挂着"。

为什么：
  - Appium server 是个进程，跟设备是 1 对 N（一台 Appium 可能服务多台 adb 上的设备）。
  - 如果只看 /status，Appium 在跑，所有挂在它名下的 udid 都会被判"心跳正常"——
    即使其中某台模拟器已经被我用户手动关掉了（典型场景：开了 5554/5556 两台模拟
    器，关掉 5556，前端依然显示绿）。
  - 真正的状态来源是设备侧的 list：
      Android → adb devices
      iOS 真机 → idevice_id -l（libimobiledevice）
      iOS 模拟器 → xcrun simctl list devices booted
    udid 不在对应清单里 = 真离线。

设计：
  - 全部走 subprocess + 短超时（3s）；命令不存在时返回"工具缺失"，让上层降级到只看 Appium。
  - 不做缓存：每次都现拉清单，30s 一次的频率不会压垮 adb。
  - iOS 真机 / 模拟器自动判别：udid 是 UUID 风格（含 8-4-4-4-12 横线）→ 模拟器；否则 → 真机。

返回 `(ok, detail, supported)`：
  - ok = True / False / None（None 表示无法判断，比如工具缺失或平台未识别）
  - detail = 给前端看的可读说明
  - supported = False 表示当前环境根本没办法做设备级探测（adb / idevice_id 都缺）
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional, Tuple

from utils.logger import LOGGER


# UUID 风格 udid（iOS 模拟器）：8-4-4-4-12 hex，带横线
_IOS_SIM_UDID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _run(cmd: list[str], timeout: float = 3.0) -> Tuple[int, str, str]:
    """跑一条短命令，返回 (returncode, stdout, stderr)。任何异常吞掉、当 returncode=-1。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout: {' '.join(cmd)}"
    except Exception as exc:  # noqa: BLE001
        return -1, "", f"{type(exc).__name__}: {exc}"


def _list_adb_devices() -> Optional[set[str]]:
    """返回当前 adb 看到的"在线"设备 udid 集合。adb 不在 PATH 时返回 None。"""
    if not shutil.which("adb"):
        return None
    rc, out, err = _run(["adb", "devices"])
    if rc != 0:
        LOGGER.debug("adb devices 失败 rc=%s err=%s", rc, err.strip()[:200])
        return set()
    udids: set[str] = set()
    # 输出格式：
    #   List of devices attached
    #   emulator-5554   device
    #   emulator-5556   offline
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        udid, state = parts[0], parts[1]
        # state 为 "device" 才算真活；offline / unauthorized / no permissions 都不算
        if state == "device":
            udids.add(udid)
    return udids


def _list_idevice_ids() -> Optional[set[str]]:
    """libimobiledevice：iOS 真机 udid 列表。idevice_id 不在 PATH 时返回 None。"""
    if not shutil.which("idevice_id"):
        return None
    rc, out, _ = _run(["idevice_id", "-l"])
    if rc != 0:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _list_booted_simulators() -> Optional[set[str]]:
    """xcrun simctl 列出当前 booted 的 iOS 模拟器 udid。simctl 不可用时返回 None。"""
    if not shutil.which("xcrun"):
        return None
    rc, out, _ = _run(["xcrun", "simctl", "list", "devices", "booted"])
    if rc != 0:
        return set()
    udids: set[str] = set()
    # 输出每一行带括号的 udid，例：iPhone 15 Pro (XXXX-...) (Booted)
    for line in out.splitlines():
        m = re.search(r"\(([0-9A-Fa-f-]{36})\)\s*\(Booted\)", line)
        if m:
            udids.add(m.group(1))
    return udids


def probe_device_alive(udid: str, platform: str) -> Tuple[Optional[bool], str, bool]:
    """探一台设备是否在系统层面"看得见"。

    参数：
      udid     设备唯一标识，跟 DB.devices.udid 对应
      platform "Android" / "iOS"（大小写不敏感）

    返回：
      (ok, detail, supported)
      ok=True  设备在线
      ok=False 工具能查但 udid 不在清单里 = 离线
      ok=None  没装相应的工具 / 平台无法识别 → 无法判断（不要凭这个翻 offline）
    """
    udid = (udid or "").strip()
    plat = (platform or "").strip().lower()
    if not udid:
        return None, "udid 为空，跳过设备级探测", False

    if plat in ("android", "andriod"):  # 容忍历史拼写
        seen = _list_adb_devices()
        if seen is None:
            return None, "adb 未安装，跳过 Android 设备级探测", False
        if udid in seen:
            return True, "adb devices: device", True
        return False, f"adb devices 里看不到 {udid}（设备未连接 / offline / unauthorized）", True

    if plat == "ios":
        # 模拟器 vs 真机：UUID 风格视作模拟器
        if _IOS_SIM_UDID_RE.match(udid):
            seen = _list_booted_simulators()
            if seen is None:
                return None, "xcrun 未安装，跳过 iOS 模拟器探测", False
            if udid in seen:
                return True, "simctl: Booted", True
            return False, f"模拟器未启动（simctl list 中没找到 {udid}）", True
        # 真机
        seen = _list_idevice_ids()
        if seen is None:
            return None, "idevice_id 未安装，跳过 iOS 真机探测", False
        if udid in seen:
            return True, "idevice_id 在线", True
        return False, f"idevice_id -l 里看不到 {udid}（线缆 / 信任问题）", True

    return None, f"未识别的平台 platform={platform!r}", False
