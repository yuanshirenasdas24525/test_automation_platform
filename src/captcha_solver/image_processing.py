# -*- coding:utf-8 -*-
from typing import Any, Dict, Tuple, List, Literal
import base64
import cv2
import numpy as np


def _smooth_signal(signal: np.ndarray, kernel_size: int = 11) -> np.ndarray:
    """一维信号滑动平均降噪（kernel_size 自动取奇数且不小于 3）"""
    k = max(3, kernel_size | 1)
    kernel = np.ones(k, dtype=np.float64) / k
    return np.convolve(signal, kernel, mode="same")


def _chi_square_hist_distance(region_a: np.ndarray, region_b: np.ndarray, bins: int = 32) -> float:
    """三通道直方图的卡方距离（越大越不同）"""
    dist = 0.0
    for ch in range(3):
        h1, _ = np.histogram(region_a[:, :, ch], bins=bins, range=(0, 256))
        h2, _ = np.histogram(region_b[:, :, ch], bins=bins, range=(0, 256))
        h1 = h1.astype(np.float64) + 1e-6
        h2 = h2.astype(np.float64) + 1e-6
        h1 /= h1.sum()
        h2 /= h2.sum()
        dist += 0.5 * np.sum((h1 - h2) ** 2 / (h1 + h2))
    return float(dist)


def _verify_split_line(
    image_bgr: np.ndarray,
    coordinate: int,
    axis: Literal["y", "x"] = "y",
    margin: int = 10,
    threshold: float = 0.05,
) -> bool:
    """
    二次验证：在 split 附近做小窗口区域两侧直方图差异，过滤误检。
    axis="y" 验证横线；axis="x" 验证竖线。
    """
    height, width = image_bgr.shape[:2]
    if axis == "y":
        y = coordinate
        y0, y1 = max(0, y - margin), min(height, y + margin)
        top_part, bottom_part = image_bgr[:y0, :], image_bgr[y1:, :]
        if top_part.size == 0 or bottom_part.size == 0:
            return False
        diff = _chi_square_hist_distance(top_part, bottom_part)
        return diff > threshold
    else:
        x = coordinate
        x0, x1 = max(0, x - margin), min(width, x + margin)
        left_part, right_part = image_bgr[:, :x0], image_bgr[:, x1:]
        if left_part.size == 0 or right_part.size == 0:
            return False
        diff = _chi_square_hist_distance(left_part, right_part)
        return diff > threshold


def _detect_split_single_scale(
    image_bgr: np.ndarray,
    smooth_kernel: int = 21,
    top_k: int = 5,
) -> Tuple[int, int]:
    """
    单尺度检测：先找横向 split_row，再在上半区找竖向 split_col。
    返回 (split_row, split_col) 像素坐标。
    """
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # —— 横线候选（行投影结合 Canny 边缘）——
    edges = cv2.Canny(gray, 50, 150)
    row_projection = np.sum(edges, axis=1)
    row_projection_smoothed = _smooth_signal(row_projection, kernel_size=smooth_kernel)
    row_min, row_max = height // 3, (height * 2) // 3  # 中间 1/3 区域
    row_roi = row_projection_smoothed[row_min:row_max]

    cand_row_idx = np.argpartition(row_roi, -top_k)[-top_k:]
    row_candidates = (cand_row_idx + row_min).tolist()

    best_row, best_row_score = None, -1.0
    for r in row_candidates:
        top_part, bottom_part = image_bgr[:r, :], image_bgr[r:, :]
        if top_part.size == 0 or bottom_part.size == 0:
            continue
        score = _chi_square_hist_distance(top_part, bottom_part)
        if score > best_row_score:
            best_row_score, best_row = score, r
    split_row = best_row if best_row is not None else int(np.argmax(row_roi) + row_min)

    # —— 竖线候选（列投影结合 Sobel X）——
    upper_bgr = image_bgr[:split_row, :]
    upper_gray = gray[:split_row, :]
    sobel_x = cv2.Sobel(upper_gray, cv2.CV_64F, 1, 0, ksize=3)
    sobel_x_abs = np.abs(sobel_x)
    col_projection = np.sum(sobel_x_abs, axis=0)
    col_projection_smoothed = _smooth_signal(col_projection, kernel_size=smooth_kernel)
    col_min, col_max = width // 4, (width * 3) // 4  # 中间 1/2 区域
    col_roi = col_projection_smoothed[col_min:col_max]

    cand_col_idx = np.argpartition(col_roi, -top_k)[-top_k:]
    col_candidates = (cand_col_idx + col_min).tolist()

    best_col, best_col_score = None, -1.0
    for c in col_candidates:
        left_part, right_part = upper_bgr[:, :c], upper_bgr[:, c:]
        if left_part.size == 0 or right_part.size == 0:
            continue
        # 差异分 + 垂直边覆盖率（偏好更“连续”的竖线）
        score = _chi_square_hist_distance(left_part, right_part)
        column_strength = sobel_x_abs[:, c]
        coverage = float(np.count_nonzero(column_strength > column_strength.mean())) / len(column_strength)
        score *= (0.5 + coverage)
        if score > best_col_score:
            best_col_score, best_col = score, c
    split_col = best_col if best_col is not None else int(np.argmax(col_roi) + col_min)

    return int(split_row), int(split_col)


def detect_split_position(
    background_image_b64: str,
    output_path: str = "annotated.jpg",
) -> Dict[str, Any]:
    """
    多尺度检测滑块背景的分割位置，并在图上画出 split 行列线。
    返回:
      {
        "full_size": (width, height),
        "split_row": int,
        "split_col": int,
        "saved_path": str,
      }
    """
    # 1) 解码 Base64 → BGR
    b64 = background_image_b64.split(",", 1)[1] if background_image_b64.startswith("data:image") else background_image_b64
    img_bytes = base64.b64decode(b64)
    img_np = np.frombuffer(img_bytes, dtype=np.uint8)
    image_bgr = cv2.imdecode(img_np, cv2.IMREAD_COLOR)
    if image_bgr is None:
        raise ValueError("无法解码图片（Base64 内容无效或 OpenCV 不支持）")

    height, width = image_bgr.shape[:2]

    # 2) 多尺度检测（稳健性更高）
    scales = [1.0, 0.5, 0.25]
    row_votes: List[int] = []
    col_votes: List[int] = []
    for s in scales:
        resized = cv2.resize(image_bgr, (int(width * s), int(height * s)))
        row_s, col_s = _detect_split_single_scale(resized)
        row_votes.append(int(row_s / s))
        col_votes.append(int(col_s / s))

    split_row = int(np.median(row_votes))
    split_col = int(np.median(col_votes))

    # 3) 二次验证，不通过就退回“投票结果”
    if not _verify_split_line(image_bgr, split_row, axis="y"):
        split_row = max(row_votes, key=row_votes.count)
    if not _verify_split_line(image_bgr, split_col, axis="x"):
        split_col = max(col_votes, key=col_votes.count)

    # 4) 标注并保存
    cv2.line(image_bgr, (0, split_row), (width, split_row), (0, 0, 255), 2)
    cv2.line(image_bgr, (split_col, 0), (split_col, height), (0, 255, 0), 2)
    cv2.imwrite(output_path, image_bgr)

    return {
        "full_size": (width, height),
        "split_row": split_row,
        "split_col": split_col,
        "saved_path": output_path,
    }