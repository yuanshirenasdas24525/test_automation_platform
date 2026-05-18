# -*- coding:utf-8 -*-
import time
import requests
from utils.logger import LOGGER
from utils.reload_config import config_center
from utils.captcha.request_builder import build_slider_request
from database.redis import clear_cache
from config.settings import ProjectPaths

bg_annotated = ProjectPaths.IMG_DIR / f"bg_annotated.jpg"


def _get_host_url() -> str:
    return config_center.get("host", "url", default="http://127.0.0.1:54351")


def _get_headers() -> dict:
    return config_center.get("header", default={})


def gen_captcha(max_retries=15):
    """调用 /gen 获取验证码，失败时仅在返回失败时清理 Redis 缓存"""
    host = _get_host_url()
    headers = _get_headers()
    gen_url = f"{host}/api/forex-user/v2/user/captcha/gen"
    for attempt in range(max_retries):
        try:
            resp = requests.post(gen_url, headers=headers, json={"type": "SLIDER"}).json()
        except Exception as e:
            LOGGER.error(f"[Gen] 请求异常: {e}, 第 {attempt+1} 次重试")
            time.sleep(0.5)
            continue

        # 正常返回并包含验证码数据
        captcha_data = resp.get("data", {}).get("captcha", {}).get("data")
        if resp.get("success") and captcha_data:
            return resp

        # 如果明确返回失败（success=False），清理缓存
        if not resp.get("success", True):
            LOGGER.warning(f"[Gen] 生成验证码失败，第 {attempt+1} 次重试，清理缓存")
            clear_cache("captcha*")
        else:
            LOGGER.warning(f"[Gen] 生成验证码失败，第 {attempt+1} 次重试")

        time.sleep(0.5)

    raise RuntimeError("无法生成验证码")

def check_captcha(payload: dict):
    """调用 /check 校验验证码"""
    host = _get_host_url()
    headers = _get_headers()
    check_url = f"{host}/api/forex-user/v2/user/captcha/check"
    resp = requests.post(check_url, headers=headers, json=payload).json()
    if resp.get("errorCode", {}):
        clear_cache("captcha*")
    return resp


def solve_captcha():
    """完整流程：生成 → 分析图片 → 轨迹 → 校验"""
    while True:
        r = gen_captcha()
        captcha_id = r["data"]["id"]
        bg_image_b64 = r["data"]["captcha"]["backgroundImage"]

        # 图像分析，生成轨迹
        request_data = build_slider_request(captcha_id, bg_image_b64,0, bg_annotated)

        resp = check_captcha(request_data)

        if resp.get("success"):
            LOGGER.info(f"[Solve] 验证码通过: {resp['data']['id']}")
            return resp["data"]["id"]


if __name__ == "__main__":
    print(solve_captcha())

