"""Pixel geometry for Agent measurements; no identity metadata is retained."""
import math
from pathlib import Path

from mindspine.agent_tools import AgentToolError


def calibration_for_image(path: Path, image_shape: tuple[int, int]) -> dict:
    calibration = {'calibrated': False, 'source': 'unavailable', 'pixels_per_mm': None,
                   'resample_size': None, 'reason': '未提供像素尺度标定'}
    if path.suffix.lower() not in {'.dcm', '.dicom'}:
        return calibration
    import pydicom
    try:
        dataset = pydicom.dcmread(path, stop_before_pixels=True)
        spacing = [float(value) for value in dataset.PixelSpacing]
        if len(spacing) != 2 or not all(math.isfinite(value) and 0.001 <= value <= 10 for value in spacing):
            return calibration
    except (AttributeError, TypeError, ValueError):
        return calibration
    row_mm, col_mm = spacing
    calibration.update(calibrated=True, source='DICOM PixelSpacing', pixel_spacing_mm=spacing,
                       pixels_per_mm=1 / row_mm, reason='使用 DICOM 像素间距；未进行额外影像放大率校正')
    if not math.isclose(row_mm, col_mm, rel_tol=1e-4):
        height, width = image_shape
        resized_width = max(1, round(width * col_mm / row_mm))
        if not 0.0625 <= col_mm / row_mm <= 16 or resized_width * height > 60_000_000:
            raise AgentToolError('DICOM 像素间距异常，无法进行可靠的几何测量', 422)
        calibration['resample_size'] = [resized_width, height]
        calibration['reason'] += '；已校正非等距像素几何'
    return calibration
