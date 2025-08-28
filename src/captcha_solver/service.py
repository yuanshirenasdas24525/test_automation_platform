# -*- coding:utf-8 -*-
import time
import requests
from src.utils.logger import LOGGER
from src.utils.read_file import read_conf
from src.captcha_solver.request_builder import build_slider_request
from src.utils.redis_helper import clear_captcha_cache
from config.settings import ProjectPaths

bg_annotated = ProjectPaths.IMG_DIR / f"bg_annotated.jpg"
HOST = read_conf.get_dict('host')['url']
GEN_URL = f"{HOST}/api/forex-user/v2/user/captcha/gen"
CHECK_URL = f"{HOST}/api/forex-user/v2/user/captcha/check"
HEADERS = read_conf.get_dict("header")


def gen_captcha(max_retries=15):
    """调用 /gen 获取验证码，失败时仅在返回失败时清理 Redis 缓存"""
    for attempt in range(max_retries):
        try:
            resp = requests.post(GEN_URL, headers=HEADERS, json={"type": "SLIDER"}).json()
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
            clear_captcha_cache()
        else:
            LOGGER.warning(f"[Gen] 生成验证码失败，第 {attempt+1} 次重试")

        time.sleep(0.5)

    raise RuntimeError("无法生成验证码")

def check_captcha(payload: dict):
    """调用 /check 校验验证码"""
    resp = requests.post(CHECK_URL, headers=HEADERS, json=payload).json()
    if resp.get("errorCode", {}):
        clear_captcha_cache()
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

