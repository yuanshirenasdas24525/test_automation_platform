# -*- coding:utf-8 -*-
from typing import Dict, Any
import random
from datetime import datetime, timedelta
from src.captcha_solver.image_processing import detect_split_position


def build_slider_request(
    captcha_id: str,
    background_image_b64: str,
    jitter_y: int = 0,
    annotated_path: str = "bg.jpg",
) -> Dict[str, Any]:
    """
    根据背景图的分割位置，构造滑块轨迹与时序请求体。
    - jitter_y: 轨迹在 y 方向的小随机抖动中心（像素）
    """
    split_info = detect_split_position(background_image_b64, output_path=annotated_path)

    width_px = split_info["full_size"][0]
    height_px = split_info["full_size"][1]
    split_row = split_info["split_row"]
    split_col = split_info["split_col"]

    # 服务端坐标系是你原代码的一半，保持一致（如服务端对图片做了缩放/下采样）
    bg_width_px = width_px // 2
    bg_height_px = height_px // 2
    template_height_px = split_row // 2
    target_offset_x_px = bg_width_px - (split_col // 2)

    # 拖动起止时间
    start_time = datetime.utcnow()

    # 轨迹生成
    track_events = []
    t_ms = random.randint(300, 4000)

    # 起点：按下
    track_events.append({"x": 0, "y": 0, "type": "down", "t": t_ms})

    current_x, current_y = 0, 0
    while current_x < target_offset_x_px:
        step = random.randint(5, 10)  # 右移 5~10 px
        current_x = min(target_offset_x_px, current_x + step)
        current_y = jitter_y + random.randint(-1, 1)  # 轻微抖动
        t_ms += random.randint(100, 400)  # 100~400ms
        track_events.append({"x": current_x, "y": current_y, "type": "move", "t": t_ms})

    # 抬起
    t_ms += random.randint(50, 150)
    track_events.append({"x": current_x, "y": current_y, "type": "up", "t": t_ms})
    stop_time = start_time + timedelta(milliseconds=t_ms)

    payload: Dict[str, Any] = {
        "id": captcha_id,
        "data": {
            "bgImageWidth": bg_width_px,
            "bgImageHeight": bg_height_px,
            "templateImageWidth": bg_width_px,
            "templateImageHeight": template_height_px,
            "startTime": start_time.isoformat() + "Z",
            "stopTime": stop_time.isoformat() + "Z",
            "trackList": track_events,
        },
    }
    return payload