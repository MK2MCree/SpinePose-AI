# model.py
import os
import sys
import cv2
import torch
import math
import numpy as np
import pydicom
import datetime
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO


class SpineModel:
    def __init__(self):
        self.model_xray = None
        self.model_rgb = None
        self.cls_model_xray = None
        self.cls_model_rgb = None
        self.run_device = 'cpu'

        self.current_mode = None
        self.is_batch_mode = False
        self.image_path = None
        self.folder_path = None
        self.image_list = []

        self.raw_pixel_array = None
        self.current_cv_img = None
        self.cv_image_result = None

        self.current_main_img = None
        self.current_sub_img = None
        self.last_bbox = None

        self.current_angles = None
        self.current_slip_list = []

    def load_models(self):
        self.run_device = 0 if torch.cuda.is_available() else 'cpu'

        # 【关键：这里会自动判断是否被打包成了 exe】
        # 如果是 exe，就获取 exe 所在的同级目录；如果是 python 运行，就获取当前 py 文件目录
        base_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(
            os.path.abspath(__file__))
        status_text = ""

        # 加载 X 光主模型
        weight_path = os.path.join(base_dir, "weights", "best.pt")
        if os.path.exists(weight_path):
            try:
                self.model_xray = YOLO(weight_path)
                status_text += f"X光检测就绪({self.run_device}) "
            except Exception:
                status_text += "X光检测失败 "
        else:
            status_text += "缺X光主模型 "

        # 加载 RGB 模型 (已移除你本地的 C 盘绝对路径，强制让它去 weights 文件夹找)
        bbox_weight_path = os.path.join(base_dir, "weights", "bbox_best.pt")
        if os.path.exists(bbox_weight_path):
            try:
                self.model_rgb = YOLO(bbox_weight_path)
                status_text += "| RGB检测就绪 "
            except Exception:
                status_text += "| RGB检测失败 "
        else:
            status_text += "| 缺RGB主模型 "

        cls_xray_path = os.path.join(base_dir, "weights", "xray_cls.pt")
        if os.path.exists(cls_xray_path):
            try:
                self.cls_model_xray = YOLO(cls_xray_path)
                status_text += "| 💡X光分类路由激活 "
            except Exception:
                pass

        cls_rgb_path = os.path.join(base_dir, "weights", "rgb_cls.pt")
        if os.path.exists(cls_rgb_path):
            try:
                self.cls_model_rgb = YOLO(cls_rgb_path)
                status_text += "| 💡RGB分类路由激活 "
            except Exception:
                pass

        return status_text

    def auto_flip_if_needed(self, cv_img, mode, raw_array=None):
        if cv_img is None: return cv_img, raw_array
        try:
            target_model = self.cls_model_xray if mode == "xray" else self.cls_model_rgb
            if target_model is not None:
                small_img = cv2.resize(cv_img, (224, 224))
                results = target_model.predict(source=small_img, device=self.run_device, verbose=False)
                top1_idx = results[0].probs.top1
                pred_class = results[0].names[top1_idx].lower()
                if "right" in pred_class or "右" in pred_class:
                    print(f"[{mode}] AI 分类前处理介入: 侦测到人物朝右，正在自动镜像以适配主模型学习方向...")
                    cv_img = cv2.flip(cv_img, 1)
                    if raw_array is not None:
                        raw_array = np.fliplr(raw_array)
        except Exception as e:
            print(f"自动翻转路由异常，跳过处理: {e}")
        return cv_img, raw_array

    def read_medical_image_and_scale(self, path, default_scale):
        try:
            if path.lower().endswith('.dcm'):
                dicom_data = pydicom.dcmread(path, force=True)
                if 'TransferSyntaxUID' not in dicom_data.file_meta:
                    dicom_data.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                    dicom_data.is_little_endian = True
                    dicom_data.is_implicit_VR = True
                raw_array = dicom_data.pixel_array.astype(float)
                if hasattr(dicom_data, 'RescaleSlope') and hasattr(dicom_data, 'RescaleIntercept'):
                    raw_array = raw_array * dicom_data.RescaleSlope + dicom_data.RescaleIntercept
                ww, wl = None, None
                if 'WindowWidth' in dicom_data and 'WindowCenter' in dicom_data:
                    try:
                        ww_val = dicom_data.WindowWidth
                        wl_val = dicom_data.WindowCenter
                        ww = float(ww_val[0]) if isinstance(ww_val, (list, pydicom.multival.MultiValue)) else float(
                            ww_val)
                        wl = float(wl_val[0]) if isinstance(wl_val, (list, pydicom.multival.MultiValue)) else float(
                            wl_val)
                    except Exception:
                        pass
                if ww is None or wl is None or ww <= 0:
                    p_min, p_max = np.percentile(raw_array, 1), np.percentile(raw_array, 99)
                    ww = max(p_max - p_min, 1.0)
                    wl = (p_max + p_min) / 2.0
                p_min, p_max = wl - ww / 2.0, wl + ww / 2.0
                windowed = np.clip(raw_array, p_min, p_max)
                if p_max - p_min != 0:
                    windowed = (windowed - p_min) / (p_max - p_min) * 255.0
                else:
                    windowed = np.zeros_like(windowed)
                pixel_array_8bit = windowed.astype(np.uint8)
                cv_img = cv2.cvtColor(pixel_array_8bit, cv2.COLOR_GRAY2BGR) if len(
                    pixel_array_8bit.shape) == 2 else cv2.cvtColor(pixel_array_8bit, cv2.COLOR_RGB2BGR)
                mm_per_px = None
                if hasattr(dicom_data, 'PixelSpacing') and dicom_data.PixelSpacing:
                    mm_per_px = float(dicom_data.PixelSpacing[0])
                calculated_scale = 1.0 / mm_per_px if (mm_per_px is not None and mm_per_px > 0) else default_scale
                return cv_img, calculated_scale, raw_array, ww, wl
            else:
                return cv2.imread(path), default_scale, None, None, None
        except Exception:
            return None, default_scale, None, None, None

    def update_windowing(self, wl, ww):
        if self.raw_pixel_array is None: return
        p_min, p_max = wl - ww / 2.0, wl + ww / 2.0
        windowed = np.clip(self.raw_pixel_array, p_min, p_max)
        windowed = (windowed - p_min) / (p_max - p_min) * 255.0 if p_max != p_min else np.zeros_like(windowed)
        pixel_array_8bit = windowed.astype(np.uint8)
        self.current_cv_img = cv2.cvtColor(pixel_array_8bit, cv2.COLOR_GRAY2BGR) if len(
            pixel_array_8bit.shape) == 2 else cv2.cvtColor(pixel_array_8bit, cv2.COLOR_RGB2BGR)
        self.current_main_img = self.current_cv_img
        self.current_sub_img = None
        self.cv_image_result = None

    def mirror_image(self):
        if self.current_cv_img is None: return
        self.current_cv_img = cv2.flip(self.current_cv_img, 1)
        if self.raw_pixel_array is not None: self.raw_pixel_array = np.fliplr(self.raw_pixel_array)
        if self.cv_image_result is not None: self.cv_image_result = cv2.flip(self.cv_image_result, 1)
        if self.current_main_img is not None: self.current_main_img = cv2.flip(self.current_main_img, 1)
        if self.current_sub_img is not None: self.current_sub_img = cv2.flip(self.current_sub_img, 1)

    def process_medical_image(self, result, annotated_frame, px_per_mm):
        angles_dict = {
            "LL": None,
            "SSA": None,
            "HARRISON_A": None,
            "LCI": None,
            "LL_NEAR": None,
            "LL_FAR": None,
            "SS": None,
            "LCA": None,
        }
        slip_list = []
        if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
            return annotated_frame, angles_dict, slip_list

        keypoints_raw = result.keypoints.xy.cpu().numpy()
        classes = result.boxes.cls.cpu().numpy()

        kpt_extra = None
        # ======== 核心拦截器：完全隔离新增7号点的影响 ========
        if keypoints_raw.shape[1] == 23 and len(classes) == 1 and classes[0] == 0:
            kpts_list = keypoints_raw[0]

            # 【重要修正1】：新加入的“7号点”(L3后突下沿) 在骨架定义中处于第23个（即索引22）
            # 精准剥离这个点，确保剩下的22个点原位不动！绝不影响既有椎体解剖关系。
            extra_idx = 22
            kpt_extra = kpts_list[extra_idx]

            # 严格截取前22个原始点位，保障既有主测量逻辑不受任何干扰
            kpts_22 = kpts_list[:22]

            # 重组为[num_boxes, points, 2] 格式供原逻辑使用
            keypoints = []
            for i in range(5):
                keypoints.append(np.array(kpts_22[i * 4: i * 4 + 4]))
            keypoints.append(np.array(kpts_22[20: 22]))

            valid_lumbars = [i for i in range(5) if keypoints[i][0][1] > 0]
            lumbar_indices = np.array(valid_lumbars)
            sacrum_indices = np.array([5] if keypoints[5][0][1] > 0 else [])
        else:
            keypoints = keypoints_raw
            lumbar_indices = np.where(classes == 0)[0]
            sacrum_indices = np.where(classes == 1)[0]
        # ==========================================================

        if len(lumbar_indices) == 0: return annotated_frame, angles_dict, slip_list

        sorted_lumbars = [lumbar_indices[i] for i in np.argsort([keypoints[idx][0][1] for idx in lumbar_indices])]
        upper_lumbar_idx = sorted_lumbars[0]
        sacrum_idx = sacrum_indices[0] if len(sacrum_indices) > 0 else None

        # 【重要修正2】：医生明确指示所有图片患者均面向左侧
        # 背侧恒定为右侧，强制直接锁定判定为 True，滑脱、LCA测算彻底锁死在所有骨骼右侧点
        is_back_right = True

        try:
            def calc_line_angle(p1, p2, q1, q2, acute=True):
                if any(np.all(pt == 0) for pt in (p1, p2, q1, q2)):
                    return None

                v1 = np.array([float(p2[0] - p1[0]), float(p2[1] - p1[1])], dtype=np.float64)
                v2 = np.array([float(q2[0] - q1[0]), float(q2[1] - q1[1])], dtype=np.float64)
                mag_v1 = np.linalg.norm(v1)
                mag_v2 = np.linalg.norm(v2)
                if mag_v1 == 0 or mag_v2 == 0:
                    return None

                cos_val = np.clip(np.dot(v1, v2) / (mag_v1 * mag_v2), -1.0, 1.0)
                angle_val = math.degrees(math.acos(cos_val))
                if acute and angle_val > 90:
                    angle_val = 180 - angle_val
                return round(angle_val, 1)

            if len(sorted_lumbars) >= 5:
                l3_idx, l4_idx, l5_idx = sorted_lumbars[2], sorted_lumbars[3], sorted_lumbars[4]
                l3_tl, l3_tr = keypoints[l3_idx][0], keypoints[l3_idx][1]
                l4_tl, l4_tr = keypoints[l4_idx][0], keypoints[l4_idx][1]
                l5_tl, l5_tr = keypoints[l5_idx][0], keypoints[l5_idx][1]

                angles_dict["LL_NEAR"] = calc_line_angle(l3_tl, l3_tr, l4_tl, l4_tr, acute=True)
                angles_dict["LL_FAR"] = calc_line_angle(l4_tl, l4_tr, l5_tl, l5_tr, acute=True)

            if sacrum_idx is not None:
                L1_tl, L1_tr, L1_bl, L1_br = keypoints[upper_lumbar_idx][0:4]
                S1_tl, S1_tr = keypoints[sacrum_idx][0:2]
                if not (np.all(L1_tl == 0) or np.all(S1_tl == 0)):
                    v_L1_top = np.array([L1_tr[0] - L1_tl[0], L1_tr[1] - L1_tl[1]])
                    v_S1_top = np.array([S1_tr[0] - S1_tl[0], S1_tr[1] - S1_tl[1]])
                    mag_L1, mag_S1 = np.linalg.norm(v_L1_top), np.linalg.norm(v_S1_top)
                    if mag_L1 * mag_S1 != 0:
                        cos_ll = np.clip(np.dot(v_L1_top, v_S1_top) / (mag_L1 * mag_S1), -1.0, 1.0)
                        angles_dict["LL"] = round(math.degrees(math.acos(cos_ll)), 1)
                    v_h = np.array([1, 0])
                    if mag_S1 != 0:
                        cos_ss = np.clip(np.dot(v_S1_top, v_h) / mag_S1, -1.0, 1.0)
                        ss_deg = math.degrees(math.acos(cos_ss))
                        angles_dict["SSA"] = round(180 - ss_deg if ss_deg > 90 else ss_deg, 1)
                        angles_dict["SS"] = angles_dict["SSA"]

                    # 这里的 is_back_right=True 保证 LCA 也绝对是用右侧点计算
                    p_L1_sup, p_L1_inf, p_S1_sup = (L1_tr, L1_br, S1_tr) if is_back_right else (L1_tl, L1_bl, S1_tl)
                    v_y = np.array([p_S1_sup[0] - p_L1_sup[0], p_S1_sup[1] - p_L1_sup[1]])
                    v_w = np.array([p_L1_inf[0] - p_L1_sup[0], p_L1_inf[1] - p_L1_sup[1]])
                    mag_y, mag_w = np.linalg.norm(v_y), np.linalg.norm(v_w)
                    if mag_y * mag_w != 0:
                        cos_lca = np.clip(np.dot(v_y, v_w) / (mag_y * mag_w), -1.0, 1.0)
                        angles_dict["HARRISON_A"] = round(math.degrees(math.acos(cos_lca)), 1)
                        angles_dict["LCA"] = angles_dict["HARRISON_A"]

                    cv2.line(annotated_frame, (int(L1_tl[0]), int(L1_tl[1])), (int(L1_tr[0]), int(L1_tr[1])),
                             (235, 99, 37), 2)
                    cv2.line(annotated_frame, (int(S1_tl[0]), int(S1_tl[1])), (int(S1_tr[0]), int(S1_tr[1])),
                             (235, 99, 37), 2)
                    c_S1_x, c_S1_y = int((S1_tl[0] + S1_tr[0]) / 2), int((S1_tl[1] + S1_tr[1]) / 2)
                    cv2.line(annotated_frame, (c_S1_x - 100, c_S1_y), (c_S1_x + 100, c_S1_y), (38, 38, 220), 2)
                    cv2.line(annotated_frame, (int(p_L1_sup[0]), int(p_L1_sup[1])),
                             (int(p_S1_sup[0]), int(p_S1_sup[1])), (6, 119, 217), 2)

                    # --- 【重要修正3】：精准执行 LCI 计算 ---
                    if kpt_extra is not None:
                        # 患者面向左，背侧在右侧，固定取右侧顶点 _tr
                        p_L1_post_sup = L1_tr
                        p_S1_post_sup = S1_tr

                        if not (np.all(kpt_extra == 0) or np.all(p_L1_post_sup == 0) or np.all(p_S1_post_sup == 0)):
                            x1, y1 = float(p_S1_post_sup[0]), float(p_S1_post_sup[1])
                            x2, y2 = float(p_L1_post_sup[0]), float(p_L1_post_sup[1])
                            x0, y0 = float(kpt_extra[0]), float(kpt_extra[1])

                            denom = math.hypot(x2 - x1, y2 - y1)
                            if denom != 0:
                                # L3后突下沿(7号点) 到 S1与L1右顶点连线 的垂直距离
                                num = abs((x2 - x1) * (y1 - y0) - (x1 - x0) * (y2 - y1))
                                dist_px = num / denom

                                try:
                                    scale_val = float(px_per_mm)
                                    if scale_val <= 0: scale_val = 1.5
                                except:
                                    scale_val = 1.5

                                lci_mm = dist_px / scale_val
                                angles_dict["LCI"] = round(lci_mm, 1)

                                # 绘制 LCI 计算参考线
                                cv2.line(annotated_frame, (int(x1), int(y1)), (int(x2), int(y2)), (200, 150, 255), 1,
                                         cv2.LINE_AA)
                                ux = (x2 - x1) / denom
                                uy = (y2 - y1) / denom
                                vx = x0 - x1
                                vy = y0 - y1
                                proj_len = vx * ux + vy * uy
                                proj_x = x1 + proj_len * ux
                                proj_y = y1 + proj_len * uy

                                cv2.line(annotated_frame, (int(x0), int(y0)), (int(proj_x), int(proj_y)), (255, 0, 255),
                                         2)
                                cv2.circle(annotated_frame, (int(x0), int(y0)), 4, (255, 0, 255), -1)
                    # -----------------------------

            spine_sequence = sorted_lumbars.copy()
            if sacrum_idx is not None: spine_sequence.append(sacrum_idx)
            total_lumbars = len(sorted_lumbars)

            try:
                scale_val = float(px_per_mm)
                if scale_val <= 0: scale_val = 1.5
            except:
                scale_val = 1.5

            for i in range(len(spine_sequence) - 1):
                idx_upper, idx_lower = spine_sequence[i], spine_sequence[i + 1]

                # is_back_right 已强制设为 True，滑脱算法将绝对严格地获取腰椎和骶骨的右侧点！
                if is_back_right:
                    p_a_top, p_a_bottom, p_b_top = keypoints[idx_upper][1], keypoints[idx_upper][3], \
                        keypoints[idx_lower][1]
                else:
                    p_a_top, p_a_bottom, p_b_top = keypoints[idx_upper][0], keypoints[idx_upper][2], \
                        keypoints[idx_lower][0]

                if np.all(p_a_top == 0) or np.all(p_a_bottom == 0) or np.all(p_b_top == 0): continue

                x1, y1 = float(p_a_top[0]), float(p_a_top[1])
                x2, y2 = float(p_a_bottom[0]), float(p_a_bottom[1])
                x0, y0 = float(p_b_top[0]), float(p_b_top[1])

                dx = x2 - x1
                dy = y2 - y1
                mag_line = math.hypot(dx, dy)
                if mag_line == 0: continue

                ux = dx / mag_line
                uy = dy / mag_line

                vx = x0 - x1
                vy = y0 - y1

                proj_len = vx * ux + vy * uy
                proj_x = x1 + proj_len * ux
                proj_y = y1 + proj_len * uy

                dist_px_abs = math.hypot(x0 - proj_x, y0 - proj_y)

                cross_prod = dx * vy - dy * vx

                if is_back_right:
                    sign = -1 if cross_prod > 0 else 1
                else:
                    sign = 1 if cross_prod > 0 else -1

                dist_px = dist_px_abs * sign
                dist_mm = dist_px / scale_val

                is_ant, is_retro = dist_mm < -3.0, dist_mm > 3.0
                is_slip = is_ant or is_retro
                seg_name = "L5-S1" if idx_lower == sacrum_idx else f"L{5 - (total_lumbars - i) + 1}-L{5 - (total_lumbars - (i + 1)) + 1}"

                slip_list.append({
                    "segment": seg_name,
                    "distance": round(dist_mm, 1),
                    "is_slip": is_slip,
                    "is_ant": is_ant,
                    "is_retro": is_retro
                })

                line_color = (0, 0, 255) if is_slip else (0, 255, 0)

                cv2.line(annotated_frame, (int(x1 - ux * 10), int(y1 - uy * 10)),
                         (int(x2 + ux * 20), int(y2 + uy * 20)), (255, 255, 0), 2)
                cv2.line(annotated_frame, (int(x0 - ux * 20), int(y0 - uy * 20)),
                         (int(x0 + ux * 40), int(y0 + uy * 40)), (0, 165, 255), 2)
                cv2.line(annotated_frame, (int(x0), int(y0)), (int(proj_x), int(proj_y)), line_color, 2)

                if is_slip:
                    cv2.putText(annotated_frame, f"{'Ant' if is_ant else 'Retro'} {abs(dist_mm):.1f}mm",
                                (int(x0) + 15, int(y0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        except Exception as e:
            print(f"测算时出错: {e}")
        return annotated_frame, angles_dict, slip_list

    def crop_rgb_image(self):
        x1, y1, x2, y2 = self.last_bbox
        pad = 15
        h, w = self.current_cv_img.shape[:2]
        c_x1, c_y1 = max(0, x1 - pad), max(0, y1 - pad)
        c_x2, c_y2 = min(w, x2 + pad), min(h, y2 + pad)

        self.current_cv_img = self.current_cv_img[c_y1:c_y2, c_x1:c_x2].copy()
        if self.raw_pixel_array is not None:
            self.raw_pixel_array = self.raw_pixel_array[c_y1:c_y2, c_x1:c_x2].copy()
        self.current_main_img = self.current_cv_img
        self.current_sub_img = self.cv_image_result
        self.last_bbox = None

    def crop_rgb_image(self):
        x1, y1, x2, y2 = self.last_bbox
        pad = 15
        h, w = self.current_cv_img.shape[:2]
        c_x1, c_y1 = max(0, x1 - pad), max(0, y1 - pad)
        c_x2, c_y2 = min(w, x2 + pad), min(h, y2 + pad)

        self.current_cv_img = self.current_cv_img[c_y1:c_y2, c_x1:c_x2].copy()
        if self.raw_pixel_array is not None:
            self.raw_pixel_array = self.raw_pixel_array[c_y1:c_y2, c_x1:c_x2].copy()
        self.current_main_img = self.current_cv_img
        self.current_sub_img = self.cv_image_result
        self.last_bbox = None

    def _get_chinese_font(self, size):
        font_paths = [
            os.getenv("MINDSPINE_FONT_PATH", ""),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "msyh.ttc",
            "msyhbd.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "STHeiti Medium.ttc",
            "Arial Unicode.ttf",
        ]
        for path in font_paths:
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                continue
        return ImageFont.load_default()

    def _draw_centered_text(self, draw, canvas_width, y, text, font, color=(0, 0, 0)):
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text(((canvas_width - text_w) / 2, y), text, fill=color, font=font)

    def _wrap_text(self, draw, text, font, max_width):
        lines = []
        paragraphs = str(text or "").split("\n")
        for paragraph in paragraphs:
            if not paragraph:
                lines.append("")
                continue

            current = ""
            for ch in paragraph:
                trial = current + ch
                trial_box = draw.textbbox((0, 0), trial, font=font)
                trial_w = trial_box[2] - trial_box[0]

                if trial_w <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = ch

            if current:
                lines.append(current)

        return lines or [""]

    def _draw_paragraph(self, draw, x, y, text, font, max_width, color=(0, 0, 0), line_gap=12):
        lines = self._wrap_text(draw, text, font, max_width)
        sample_box = draw.textbbox((0, 0), "测试", font=font)
        line_h = (sample_box[3] - sample_box[1]) + line_gap

        for line in lines:
            draw.text((x, y), line, fill=color, font=font)
            y += line_h
        return y

    def _paste_report_image(self, report, draw, y_cursor, max_width, max_height):
        a4_w = report.width

        if self.current_main_img is None:
            draw.rectangle(
                [180, y_cursor, a4_w - 180, y_cursor + 520],
                outline=(210, 210, 210),
                width=3
            )
            hint_font = self._get_chinese_font(42)
            self._draw_centered_text(
                draw,
                a4_w,
                y_cursor + 220,
                "当前没有可插入的分析图像，请先完成图像分析后再导出报告。",
                hint_font,
                (120, 120, 120),
            )
            return y_cursor + 520

        img_rgb = cv2.cvtColor(self.current_main_img, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)

        if hasattr(Image, "Resampling"):
            lanczos = Image.Resampling.LANCZOS
        else:
            lanczos = Image.LANCZOS

        img_pil.thumbnail((max_width, max_height), lanczos)
        img_x = int((a4_w - img_pil.width) / 2)
        report.paste(img_pil, (img_x, y_cursor))
        draw.rectangle(
            [img_x, y_cursor, img_x + img_pil.width, y_cursor + img_pil.height],
            outline=(200, 200, 200),
            width=3
        )
        return y_cursor + img_pil.height

    def _build_report_context(self):
        angles = self.current_angles or {}
        slips = self.current_slip_list or []

        def fmt_angle(value):
            return f"{float(value):.1f}°" if value is not None else "--"

        def fmt_mm(value):
            return f"{float(value):.1f} mm" if value is not None else "--"

        ll = angles.get("LL")
        ssa = angles.get("SSA", angles.get("SS"))
        harrison_a = angles.get("HARRISON_A", angles.get("LCA"))
        lci = angles.get("LCI")
        ll_near = angles.get("LL_NEAR")
        ll_far = angles.get("LL_FAR")

        abnormal_slips = []
        stable_slips = []

        for item in slips:
            segment = item.get("segment", "--")
            distance = float(item.get("distance", 0.0))

            if item.get("is_ant"):
                abnormal_slips.append(f"{segment} 提示前滑倾向（{distance:+.1f} mm）")
            elif item.get("is_retro"):
                abnormal_slips.append(f"{segment} 提示后滑倾向（{distance:+.1f} mm）")
            else:
                stable_slips.append(f"{segment} 未见明显异常位移（{distance:+.1f} mm）")

        has_measurement = any(v is not None for v in [ll, ssa, harrison_a, lci, ll_near, ll_far])

        if abnormal_slips:
            level = "attention"
            accent_color = (220, 38, 38)
            headline = "本次 AI 分析提示部分节段需要重点关注"
            summary_text = "AI 在部分腰骶节段观察到前后位移征象，建议尽快携带本报告到骨科或脊柱专科复核。"
            next_steps = [
                "建议保留本次报告与原始图像，便于门诊对照查看。",
                "若近期伴随腰痛、下肢麻木、体态改变等症状，请尽快就医。",
                "是否需要进一步进行 DR、CT、MRI 等检查，请以医生意见为准。",
            ]
            doctor_impression = "AI 提示部分腰骶节段存在滑脱相关影像征象，建议结合原始影像、体征及专科医师判断进行复核。"
        elif has_measurement:
            level = "stable"
            accent_color = (16, 185, 129)
            headline = "本次 AI 分析未见明显滑脱异常"
            summary_text = "本次自动测算已完成，当前未见明显滑脱异常提示，可作为个人健康参考和复诊携带材料。"
            next_steps = [
                "如无明显不适，可保留本次报告作为阶段性记录。",
                "如后续症状加重，建议复拍影像并与本次结果对比。",
                "如需进一步诊断或治疗，请到正规医院由专科医生判断。",
            ]
            doctor_impression = "AI 自动测算已完成，本次分析范围内未见明显滑脱异常影像征象。建议结合临床症状综合判断。"
        else:
            level = "insufficient"
            accent_color = (245, 158, 11)
            headline = "本次图像未完成完整稳定测量"
            summary_text = "可能受拍摄角度、视野范围或图像质量影响，当前结果更适合参考，不建议单独据此判断。"
            next_steps = [
                "建议重新拍摄图像，并尽量保证腰骶部结构完整显示。",
                "如存在明显疼痛或活动受限，建议直接到医院检查。",
                "后续再次导出报告时，可与本次结果进行前后对比。",
            ]
            doctor_impression = "当前图像条件下未完成完整稳定的自动测量，建议优化影像质量或补充标准影像后再评估。"

        metric_items = [
            {
                "name": "LL",
                "label": "腰椎前凸角",
                "value": fmt_angle(ll),
                "desc": "反映腰椎自然前凸程度，用于观察腰椎整体弧度。",
            },
            {
                "name": "SSA",
                "label": "骶骨倾斜角",
                "value": fmt_angle(ssa),
                "desc": "反映骶骨上缘相对水平面的倾斜情况。",
            },
            {
                "name": "Harrison A",
                "label": "椎体后缘切线角",
                "value": fmt_angle(harrison_a),
                "desc": "反映腰椎后缘切线关系，用于辅助评估腰椎曲度变化。",
            },
            {
                "name": "LCI",
                "label": "腰椎曲线指数",
                "value": fmt_mm(lci),
                "desc": "反映腰椎曲线相关距离变化，仅供辅助参考。",
            },
        ]

        segmental_angle_items = [
            {
                "label": "近端节段角（L3-L4）",
                "value": fmt_angle(ll_near),
                "desc": "由 L3 顶板连线与 L4 顶板连线之间的夹角计算得到。",
            },
            {
                "label": "远端节段角（L4-L5）",
                "value": fmt_angle(ll_far),
                "desc": "由 L4 顶板连线与 L5 顶板连线之间的夹角计算得到。",
            },
        ]

        if abnormal_slips:
            patient_slip_summary = "AI 在以下节段观察到需要医生重点复核的位移倾向：" + "；".join(abnormal_slips)
        elif stable_slips:
            patient_slip_summary = "本次自动分析未见明显滑脱异常提示。"
        else:
            patient_slip_summary = "本次图像未完成完整节段滑脱分析。"

        if slips:
            slip_lines_for_doctor = abnormal_slips + stable_slips
        else:
            slip_lines_for_doctor = ["未能在有效视野内完成完整节段滑脱分析。"]

        return {
            "level": level,
            "accent_color": accent_color,
            "headline": headline,
            "summary_text": summary_text,
            "next_steps": next_steps,
            "doctor_impression": doctor_impression,
            "metric_items": metric_items,
            "segmental_angle_items": segmental_angle_items,
            "abnormal_slips": abnormal_slips,
            "stable_slips": stable_slips,
            "slip_lines_for_doctor": slip_lines_for_doctor,
            "patient_slip_summary": patient_slip_summary,
        }

    def _export_doctor_pdf_report(self, save_path):
        a4_w, a4_h = 2480, 3508
        report = Image.new("RGB", (a4_w, a4_h), "white")
        draw = ImageDraw.Draw(report)
        context = self._build_report_context()

        font_title = self._get_chinese_font(88)
        font_sub = self._get_chinese_font(56)
        font_body = self._get_chinese_font(40)
        font_bold = self._get_chinese_font(48)
        font_small = self._get_chinese_font(32)

        y_cursor = 180
        self._draw_centered_text(draw, a4_w, y_cursor, "MindSpine AI 脊柱辅助分析报告", font_title)
        y_cursor += 120
        self._draw_centered_text(draw, a4_w, y_cursor, "医生版 / 专业阅片参考", font_sub, (70, 70, 70))
        y_cursor += 110

        draw.line([(180, y_cursor), (a4_w - 180, y_cursor)], fill=(0, 0, 0), width=4)
        y_cursor += 40

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        check_id = f"MS-{datetime.datetime.now().strftime('%m%d%H%M')}"
        draw.text((180, y_cursor), f"报告编号：{check_id}", fill=(0, 0, 0), font=font_body)
        draw.text((1200, y_cursor), f"导出日期：{today_str}", fill=(0, 0, 0), font=font_body)
        y_cursor += 60
        draw.text((180, y_cursor), "适用对象：骨科 / 脊柱外科 / 康复科医生", fill=(0, 0, 0), font=font_body)
        y_cursor += 80

        draw.line([(180, y_cursor), (a4_w - 180, y_cursor)], fill=(0, 0, 0), width=4)
        y_cursor += 90

        y_cursor = self._paste_report_image(report, draw, y_cursor, 2120, 1380)
        y_cursor += 100

        draw.text((180, y_cursor), "一、客观测量参数", fill=(0, 82, 155), font=font_bold)
        y_cursor += 80

        x_positions = [220, 780, 1340, 1900]
        for idx, metric in enumerate(context["metric_items"]):
            draw.text((x_positions[idx], y_cursor), metric["name"], fill=(120, 120, 120), font=font_small)
            draw.text((x_positions[idx], y_cursor + 40), metric["label"], fill=(0, 0, 0), font=font_body)
            draw.text((x_positions[idx], y_cursor + 95), metric["value"], fill=(0, 0, 0), font=font_bold)

        y_cursor += 200

        for segment_metric in context.get("segmental_angle_items", []):
            draw.text((220, y_cursor), f"{segment_metric['label']}：{segment_metric['value']}", fill=(0, 0, 0),
                      font=font_body)
            y_cursor += 44

        y_cursor += 10

        draw.text((180, y_cursor), "二、节段位移分析", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        for line in context["slip_lines_for_doctor"]:
            color = (220, 38, 38) if ("前滑" in line or "后滑" in line) else (16, 185, 129)
            y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"• {line}", font_body, 2050, color=color, line_gap=10)
            y_cursor += 8

        y_cursor += 40
        draw.text((180, y_cursor), "三、AI 辅助印象", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"1. {context['doctor_impression']}", font_body, 2050)
        y_cursor += 10
        y_cursor = self._draw_paragraph(
            draw,
            220,
            y_cursor,
            "2. 本报告为 AI 自动测量与规则分析结果，适合用于门诊初筛、随访对比和辅助沟通，不替代临床最终诊断。",
            font_body,
            2050
        )

        footer_y = a4_h - 300
        draw.line([(180, footer_y), (a4_w - 180, footer_y)], fill=(0, 0, 0), width=4)
        footer_y += 35
        draw.text((180, footer_y), "分析系统：MindSpine Web Workbench", fill=(0, 0, 0), font=font_body)
        draw.text((1050, footer_y), "报告用途：医生阅片参考", fill=(0, 0, 0), font=font_body)
        draw.text((1710, footer_y), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(0, 0, 0), font=font_body)

        footer_y += 70
        self._draw_centered_text(
            draw,
            a4_w,
            footer_y,
            "声明：本报告由人工智能算法自动生成，仅供临床辅助参考，不作为最终医疗诊断依据。",
            font_small,
            (120, 120, 120),
        )

        report.save(save_path, "PDF", resolution=300.0)

    def _export_personal_pdf_report(self, save_path):
        a4_w, a4_h = 2480, 3508
        report = Image.new("RGB", (a4_w, a4_h), "white")
        draw = ImageDraw.Draw(report)
        context = self._build_report_context()

        accent = context["accent_color"]

        font_title = self._get_chinese_font(90)
        font_sub = self._get_chinese_font(54)
        font_body = self._get_chinese_font(42)
        font_bold = self._get_chinese_font(50)
        font_small = self._get_chinese_font(32)

        y_cursor = 170
        self._draw_centered_text(draw, a4_w, y_cursor, "脊柱 AI 健康参考报告", font_title)
        y_cursor += 120
        self._draw_centered_text(draw, a4_w, y_cursor, "个人版 / 便于自我查看与就诊携带", font_sub, (80, 80, 80))
        y_cursor += 120

        draw.rectangle([180, y_cursor, a4_w - 180, y_cursor + 280], fill=(248, 250, 252), outline=accent, width=6)
        draw.text((230, y_cursor + 35), context["headline"], fill=accent, font=font_bold)
        self._draw_paragraph(draw, 230, y_cursor + 105, context["summary_text"], font_body, a4_w - 460, color=(0, 0, 0),
                             line_gap=12)

        y_cursor += 340
        y_cursor = self._paste_report_image(report, draw, y_cursor, 1980, 1080)
        y_cursor += 90

        draw.text((180, y_cursor), "一、你可以先看这三点", fill=(0, 82, 155), font=font_bold)
        y_cursor += 75

        point_1 = context["headline"]
        point_2 = context["patient_slip_summary"]
        point_3 = "这份报告更适合做健康参考和复诊携带，不建议代替医生面诊结论。"

        y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"1. {point_1}", font_body, 2050)
        y_cursor += 6
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"2. {point_2}", font_body, 2050)
        y_cursor += 6
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"3. {point_3}", font_body, 2050)

        y_cursor += 40
        draw.text((180, y_cursor), "二、这几个数字是什么意思", fill=(0, 82, 155), font=font_bold)
        y_cursor += 75

        for metric in context["metric_items"]:
            draw.text((220, y_cursor), f"{metric['label']}：{metric['value']}", fill=(0, 0, 0), font=font_body)
            y_cursor += 48
            y_cursor = self._draw_paragraph(draw, 260, y_cursor, metric["desc"], font_small, 1940, color=(90, 90, 90),
                                            line_gap=10)
            y_cursor += 20

        for segment_metric in context.get("segmental_angle_items", []):
            draw.text((220, y_cursor), f"{segment_metric['label']}：{segment_metric['value']}", fill=(0, 0, 0),
                      font=font_body)
            y_cursor += 48
            y_cursor = self._draw_paragraph(draw, 260, y_cursor, segment_metric["desc"], font_small, 1940,
                                            color=(90, 90, 90), line_gap=10)
            y_cursor += 20

        y_cursor += 10
        draw.text((180, y_cursor), "三、接下来建议怎么做", fill=(0, 82, 155), font=font_bold)
        y_cursor += 75

        for idx, step in enumerate(context["next_steps"], start=1):
            y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"{idx}. {step}", font_body, 2050)
            y_cursor += 8

        y_cursor += 30
        draw.text((180, y_cursor), "四、温馨提示", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70
        y_cursor = self._draw_paragraph(
            draw,
            220,
            y_cursor,
            "本报告是为了让你更容易理解 AI 分析结果而设计的健康参考版。它可以帮助你提前了解情况、保存阶段性记录、在就诊时和医生沟通，但不能替代医生最终诊断。",
            font_body,
            2050
        )

        footer_y = a4_h - 300
        draw.line([(180, footer_y), (a4_w - 180, footer_y)], fill=(0, 0, 0), width=4)
        footer_y += 35
        draw.text((180, footer_y), "分析系统：MindSpine Web Workbench", fill=(0, 0, 0), font=font_body)
        draw.text((1050, footer_y), "报告用途：个人健康参考", fill=(0, 0, 0), font=font_body)
        draw.text((1710, footer_y), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(0, 0, 0), font=font_body)

        footer_y += 70
        self._draw_centered_text(
            draw,
            a4_w,
            footer_y,
            "声明：本报告为人工智能自动生成的健康参考信息，不作为最终诊疗、处方或手术决策依据。",
            font_small,
            (120, 120, 120),
        )

        report.save(save_path, "PDF", resolution=300.0)

    def _get_chinese_font(self, size):
        font_paths = [
            os.getenv("MINDSPINE_FONT_PATH", ""),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "msyh.ttc",
            "msyhbd.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "STHeiti Medium.ttc",
            "Arial Unicode.ttf",
        ]
        for path in font_paths:
            try:
                return ImageFont.truetype(path, size)
            except IOError:
                continue
        return ImageFont.load_default()

    def _draw_centered_text(self, draw, canvas_width, y, text, font, color=(0, 0, 0)):
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text(((canvas_width - text_w) / 2, y), text, fill=color, font=font)

    def _wrap_text(self, draw, text, font, max_width):
        lines = []
        paragraphs = str(text or "").split("\n")

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                lines.append("")
                continue

            current = ""
            for ch in paragraph:
                trial = current + ch
                bbox = draw.textbbox((0, 0), trial, font=font)
                width = bbox[2] - bbox[0]
                if width <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = ch

            if current:
                lines.append(current)

        return lines or [""]

    def _draw_paragraph(self, draw, x, y, text, font, max_width, color=(0, 0, 0), line_gap=12):
        lines = self._wrap_text(draw, text, font, max_width)
        sample_box = draw.textbbox((0, 0), "测试", font=font)
        line_h = (sample_box[3] - sample_box[1]) + line_gap

        for line in lines:
            draw.text((x, y), line, fill=color, font=font)
            y += line_h
        return y

    def _draw_bullet_list(self, draw, x, y, items, font, max_width, bullet_color=(0, 0, 0), text_color=(0, 0, 0)):
        if not items:
            return self._draw_paragraph(draw, x, y, "暂无内容。", font, max_width, color=text_color)

        for item in items:
            draw.text((x, y), "•", fill=bullet_color, font=font)
            y = self._draw_paragraph(draw, x + 40, y, str(item), font, max_width - 40, color=text_color)
            y += 8
        return y

    def _paste_report_image(self, report, draw, y_cursor, max_width, max_height):
        a4_w = report.width

        if self.current_main_img is None:
            draw.rectangle(
                [180, y_cursor, a4_w - 180, y_cursor + 520],
                outline=(210, 210, 210),
                width=3
            )
            hint_font = self._get_chinese_font(42)
            self._draw_centered_text(
                draw,
                a4_w,
                y_cursor + 220,
                "当前没有可插入的分析图像，请先完成图像分析后再导出报告。",
                hint_font,
                (120, 120, 120),
            )
            return y_cursor + 520

        img_rgb = cv2.cvtColor(self.current_main_img, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)

        if hasattr(Image, "Resampling"):
            lanczos = Image.Resampling.LANCZOS
        else:
            lanczos = Image.LANCZOS

        img_pil.thumbnail((max_width, max_height), lanczos)
        img_x = int((a4_w - img_pil.width) / 2)
        report.paste(img_pil, (img_x, y_cursor))
        draw.rectangle(
            [img_x, y_cursor, img_x + img_pil.width, y_cursor + img_pil.height],
            outline=(200, 200, 200),
            width=3
        )
        return y_cursor + img_pil.height

    def _new_report_page(self, title, subtitle="", accent=(0, 82, 155)):
        a4_w, a4_h = 2480, 3508
        page = Image.new("RGB", (a4_w, a4_h), "white")
        draw = ImageDraw.Draw(page)

        font_title = self._get_chinese_font(82)
        font_sub = self._get_chinese_font(46)

        self._draw_centered_text(draw, a4_w, 150, title, font_title, accent)
        if subtitle:
            self._draw_centered_text(draw, a4_w, 255, subtitle, font_sub, (90, 90, 90))

        draw.line([(180, 340), (a4_w - 180, 340)], fill=accent, width=4)
        return page, draw, 390

    def _save_pdf_pages(self, save_path, pages):
        if not pages:
            raise ValueError("No PDF pages to save.")

        rgb_pages = [page.convert("RGB") for page in pages]
        first_page = rgb_pages[0]
        extra_pages = rgb_pages[1:]

        if extra_pages:
            first_page.save(save_path, "PDF", save_all=True, append_images=extra_pages, resolution=300.0)
        else:
            first_page.save(save_path, "PDF", resolution=300.0)

    def _build_report_context(self):
        angles = self.current_angles or {}
        slips = self.current_slip_list or []

        def fmt_angle(value):
            return f"{float(value):.1f}°" if value is not None else "--"

        def fmt_mm(value):
            return f"{float(value):.1f} mm" if value is not None else "--"

        ll = angles.get("LL")
        ssa = angles.get("SSA", angles.get("SS"))
        harrison_a = angles.get("HARRISON_A", angles.get("LCA"))
        lci = angles.get("LCI")
        ll_near = angles.get("LL_NEAR")
        ll_far = angles.get("LL_FAR")

        abnormal_slips = []
        stable_slips = []

        for item in slips:
            segment = item.get("segment", "--")
            distance = float(item.get("distance", 0.0))

            if item.get("is_ant"):
                abnormal_slips.append(f"{segment} 提示前滑倾向（{distance:+.1f} mm）")
            elif item.get("is_retro"):
                abnormal_slips.append(f"{segment} 提示后滑倾向（{distance:+.1f} mm）")
            else:
                stable_slips.append(f"{segment} 未见明显异常位移（{distance:+.1f} mm）")

        has_measurement = any(v is not None for v in [ll, ssa, harrison_a, lci, ll_near, ll_far])

        if abnormal_slips:
            accent_color = (220, 38, 38)
            headline = "本次 AI 分析提示部分节段需要尽快门诊复核"
            summary_text = "AI 在部分腰骶节段观察到前后位移征象，建议尽快携带本报告到骨科或脊柱专科复核。"
            next_steps = [
                "优先挂号骨科、脊柱外科或康复医学科门诊。",
                "就诊时携带本报告、原始影像和既往相关检查资料。",
                "如伴随疼痛加重、下肢麻木或活动受限，请尽快线下就医。",
            ]
            doctor_impression = "AI 提示部分腰骶节段存在滑脱相关影像征象，建议结合原始影像、体征及专科医师判断进行复核。"
        elif has_measurement:
            accent_color = (16, 185, 129)
            headline = "本次 AI 分析未见明显滑脱异常"
            summary_text = "本次自动测算已完成，当前未见明显滑脱异常提示，可作为个人健康参考和复诊携带材料。"
            next_steps = [
                "保留本次报告作为阶段性记录，便于后续对照。",
                "如后续症状加重，建议复拍影像并和本次结果对比。",
                "如需进一步诊断或治疗，请由专科医生综合判断。",
            ]
            doctor_impression = "AI 自动测算已完成，本次分析范围内未见明显滑脱异常影像征象。建议结合临床症状综合判断。"
        else:
            accent_color = (245, 158, 11)
            headline = "本次图像未完成完整稳定测量"
            summary_text = "可能受拍摄角度、视野范围或图像质量影响，当前结果更适合作为参考，不建议单独据此判断。"
            next_steps = [
                "建议重新拍摄图像，并尽量保证腰骶部结构完整显示。",
                "如存在明显疼痛或活动受限，建议直接到医院检查。",
                "后续再次导出报告时，可与本次结果进行前后对比。",
            ]
            doctor_impression = "当前图像条件下未完成完整稳定的自动测量，建议优化影像质量或补充标准影像后再评估。"

        metric_items = [
            {"name": "LL", "label": "腰椎前凸角", "value": fmt_angle(ll), "desc": "反映腰椎整体前凸程度。"},
            {"name": "SSA", "label": "骶骨倾斜角", "value": fmt_angle(ssa), "desc": "反映骶骨上缘相对水平面的倾斜情况。"},
            {"name": "Harrison A", "label": "椎体后缘切线角", "value": fmt_angle(harrison_a), "desc": "反映腰椎后缘切线关系，用于辅助评估腰椎曲度变化。"},
            {"name": "LCI", "label": "腰椎曲线指数", "value": fmt_mm(lci), "desc": "反映腰椎曲线相关距离变化，仅供辅助参考。"},
        ]

        segmental_angle_items = [
            {"label": "近端节段角（L3-L4）", "value": fmt_angle(ll_near), "desc": "由 L3 顶板连线与 L4 顶板连线之间的夹角计算得到。"},
            {"label": "远端节段角（L4-L5）", "value": fmt_angle(ll_far), "desc": "由 L4 顶板连线与 L5 顶板连线之间的夹角计算得到。"},
        ]

        if abnormal_slips:
            patient_slip_summary = "AI 在以下节段观察到需要重点复核的位移倾向：" + "；".join(abnormal_slips)
        elif stable_slips:
            patient_slip_summary = "本次自动分析未见明显滑脱异常提示。"
        else:
            patient_slip_summary = "本次图像未完成完整节段滑脱分析。"

        slip_lines_for_doctor = abnormal_slips + stable_slips if slips else ["未能在有效视野内完成完整节段滑脱分析。"]

        return {
            "accent_color": accent_color,
            "headline": headline,
            "summary_text": summary_text,
            "next_steps": next_steps,
            "doctor_impression": doctor_impression,
            "metric_items": metric_items,
            "segmental_angle_items": segmental_angle_items,
            "abnormal_slips": abnormal_slips,
            "stable_slips": stable_slips,
            "slip_lines_for_doctor": slip_lines_for_doctor,
            "patient_slip_summary": patient_slip_summary,
        }

    def _build_personal_ai_page(self, ai_sections):
        page, draw, y_cursor = self._new_report_page(
            "DeepSeek 增强解读",
            "个人用户辅助建议页",
            accent=(94, 106, 210),
        )

        font_body = self._get_chinese_font(40)
        font_bold = self._get_chinese_font(48)
        font_small = self._get_chinese_font(32)

        status_color = (16, 185, 129) if ai_sections.get("used") else (245, 158, 11)
        urgency_map = {
            "routine": "常规门诊随诊",
            "attention": "建议尽快门诊复核",
            "soon": "建议 1-2 周内就诊",
            "urgent": "建议尽快线下就医",
        }

        draw.text((180, y_cursor), f"AI 状态：{ai_sections.get('status_message', '--')}", fill=status_color,
                  font=font_body)
        y_cursor += 65
        draw.text((180, y_cursor),
                  f"建议就医紧迫度：{urgency_map.get(ai_sections.get('urgency_level'), '常规门诊随诊')}", fill=(0, 0, 0),
                  font=font_body)
        y_cursor += 90

        draw.text((180, y_cursor), "一、AI 综合解读", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        draw.text((220, y_cursor), ai_sections.get("summary_title", "--"), fill=(0, 0, 0), font=font_body)
        y_cursor += 55
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, ai_sections.get("summary_for_patient", "--"), font_body,
                                        2050)
        y_cursor += 30

        draw.text((180, y_cursor), "二、推荐挂号科室", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_bullet_list(draw, 220, y_cursor, ai_sections.get("recommended_departments", []),
                                          font_body, 2050)
        y_cursor += 25

        draw.text((180, y_cursor), "三、推荐就医流程", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_bullet_list(draw, 220, y_cursor, ai_sections.get("visit_steps", []), font_body, 2050)
        y_cursor += 25

        draw.text((180, y_cursor), "四、生活方式建议", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_bullet_list(draw, 220, y_cursor, ai_sections.get("lifestyle_recommendations", []),
                                          font_body, 2050)
        y_cursor += 25

        draw.text((180, y_cursor), "五、训练与康复建议", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_bullet_list(draw, 220, y_cursor, ai_sections.get("rehab_recommendations", []), font_body,
                                          2050)
        y_cursor += 25

        draw.text((180, y_cursor), "六、这些情况建议尽快就医", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_bullet_list(draw, 220, y_cursor, ai_sections.get("red_flags", []), font_body, 2050,
                                          bullet_color=(220, 38, 38), text_color=(0, 0, 0))
        y_cursor += 35

        draw.text((180, y_cursor), "七、边界说明", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, ai_sections.get("medical_disclaimer", "--"), font_body,
                                        2050, color=(80, 80, 80))

        footer_y = page.height - 260
        draw.line([(180, footer_y), (page.width - 180, footer_y)], fill=(94, 106, 210), width=4)
        footer_y += 35
        draw.text((180, footer_y), "增强来源：DeepSeek API", fill=(0, 0, 0), font=font_body)
        draw.text((1080, footer_y), f"模型：{ai_sections.get('model', '--')}", fill=(0, 0, 0), font=font_body)
        draw.text((1670, footer_y), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(0, 0, 0), font=font_body)

        footer_y += 65
        self._draw_centered_text(
            draw,
            page.width,
            footer_y,
            "声明：本页为大模型辅助建议页，仅用于就医沟通和健康参考，不替代医生面诊结论。",
            font_small,
            (120, 120, 120),
        )

        return page

    def _export_doctor_pdf_report(self, save_path):
        page, draw, y_cursor = self._new_report_page(
            "MindSpine AI 脊柱辅助分析报告",
            "医生版 / 专业阅片参考",
            accent=(0, 82, 155),
        )
        context = self._build_report_context()

        font_body = self._get_chinese_font(40)
        font_bold = self._get_chinese_font(48)
        font_small = self._get_chinese_font(32)

        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        check_id = f"MS-{datetime.datetime.now().strftime('%m%d%H%M')}"

        draw.text((180, y_cursor), f"报告编号：{check_id}", fill=(0, 0, 0), font=font_body)
        draw.text((1200, y_cursor), f"导出日期：{today_str}", fill=(0, 0, 0), font=font_body)
        y_cursor += 60
        draw.text((180, y_cursor), "适用对象：骨科 / 脊柱外科 / 康复医学科医生", fill=(0, 0, 0), font=font_body)
        y_cursor += 90

        y_cursor = self._paste_report_image(page, draw, y_cursor, 2120, 1280)
        y_cursor += 80

        draw.text((180, y_cursor), "一、客观测量参数", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        for metric in context["metric_items"]:
            draw.text((220, y_cursor), f"{metric['name']} / {metric['label']}：{metric['value']}", fill=(0, 0, 0),
                      font=font_body)
            y_cursor += 48

        for segment_metric in context.get("segmental_angle_items", []):
            draw.text((220, y_cursor), f"{segment_metric['label']}：{segment_metric['value']}", fill=(0, 0, 0),
                      font=font_body)
            y_cursor += 48
        y_cursor += 25

        draw.text((180, y_cursor), "二、节段位移分析", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        for line in context["slip_lines_for_doctor"]:
            color = (220, 38, 38) if ("前滑" in line or "后滑" in line) else (16, 185, 129)
            y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"• {line}", font_body, 2050, color=color)
            y_cursor += 6

        y_cursor += 30
        draw.text((180, y_cursor), "三、AI 辅助印象", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70
        y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"1. {context['doctor_impression']}", font_body, 2050)
        y_cursor += 10
        y_cursor = self._draw_paragraph(
            draw,
            220,
            y_cursor,
            "2. 本报告为 AI 自动测量与规则分析结果，适合用于门诊初筛、随访对比和辅助沟通，不替代临床最终诊断。",
            font_body,
            2050
        )

        footer_y = page.height - 260
        draw.line([(180, footer_y), (page.width - 180, footer_y)], fill=(0, 82, 155), width=4)
        footer_y += 35
        draw.text((180, footer_y), "分析系统：MindSpine Web Workbench", fill=(0, 0, 0), font=font_body)
        draw.text((1050, footer_y), "报告用途：医生阅片参考", fill=(0, 0, 0), font=font_body)
        draw.text((1710, footer_y), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(0, 0, 0), font=font_body)

        footer_y += 65
        self._draw_centered_text(
            draw,
            page.width,
            footer_y,
            "声明：本报告由人工智能自动生成，仅供临床辅助参考，不作为最终医疗诊断依据。",
            font_small,
            (120, 120, 120),
        )

        self._save_pdf_pages(save_path, [page])

    def _export_personal_pdf_report(self, save_path, ai_sections=None):
        page, draw, y_cursor = self._new_report_page(
            "脊柱 AI 健康参考报告",
            "个人版 / 便于自我查看与就诊携带",
            accent=(0, 82, 155),
        )
        context = self._build_report_context()

        font_body = self._get_chinese_font(42)
        font_bold = self._get_chinese_font(50)
        font_small = self._get_chinese_font(32)

        draw.rectangle([180, y_cursor, page.width - 180, y_cursor + 260], fill=(248, 250, 252),
                       outline=context["accent_color"], width=6)
        draw.text((230, y_cursor + 30), context["headline"], fill=context["accent_color"], font=font_bold)
        self._draw_paragraph(draw, 230, y_cursor + 95, context["summary_text"], font_body, page.width - 460,
                             color=(0, 0, 0), line_gap=12)

        y_cursor += 310
        if ai_sections is not None:
            draw.text((180, y_cursor), f"增强状态：{ai_sections.get('status_message', '--')}", fill=(94, 106, 210),
                      font=font_small)
            y_cursor += 55

        y_cursor = self._paste_report_image(page, draw, y_cursor, 1960, 940)
        y_cursor += 80

        draw.text((180, y_cursor), "一、你可以先看这三点", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        points = [
            context["headline"],
            context["patient_slip_summary"],
            "这份报告更适合作为健康参考和就诊携带材料，不建议代替医生面诊结论。",
        ]

        for idx, point in enumerate(points, start=1):
            y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"{idx}. {point}", font_body, 2050)
            y_cursor += 8

        y_cursor += 25
        draw.text((180, y_cursor), "二、这些指标是什么意思", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        for metric in context["metric_items"]:
            draw.text((220, y_cursor), f"{metric['label']}：{metric['value']}", fill=(0, 0, 0), font=font_body)
            y_cursor += 46
            y_cursor = self._draw_paragraph(draw, 260, y_cursor, metric["desc"], font_small, 1940, color=(90, 90, 90),
                                            line_gap=10)
            y_cursor += 18

        for segment_metric in context.get("segmental_angle_items", []):
            draw.text((220, y_cursor), f"{segment_metric['label']}：{segment_metric['value']}", fill=(0, 0, 0),
                      font=font_body)
            y_cursor += 46
            y_cursor = self._draw_paragraph(draw, 260, y_cursor, segment_metric["desc"], font_small, 1940,
                                            color=(90, 90, 90), line_gap=10)
            y_cursor += 18

        y_cursor += 10
        draw.text((180, y_cursor), "三、接下来建议怎么做", fill=(0, 82, 155), font=font_bold)
        y_cursor += 70

        for idx, step in enumerate(context["next_steps"], start=1):
            y_cursor = self._draw_paragraph(draw, 220, y_cursor, f"{idx}. {step}", font_body, 2050)
            y_cursor += 8

        y_cursor += 20
        draw.text((180, y_cursor), "四、温馨提示", fill=(0, 82, 155), font=font_bold)
        y_cursor += 65
        y_cursor = self._draw_paragraph(
            draw,
            220,
            y_cursor,
            "本报告是为了让你更容易理解 AI 分析结果而设计的健康参考版。它可以帮助你提前了解情况、保存阶段性记录、在就诊时和医生沟通，但不能替代医生最终诊断。",
            font_body,
            2050
        )

        footer_y = page.height - 260
        draw.line([(180, footer_y), (page.width - 180, footer_y)], fill=(0, 82, 155), width=4)
        footer_y += 35
        draw.text((180, footer_y), "分析系统：MindSpine Web Workbench", fill=(0, 0, 0), font=font_body)
        draw.text((1050, footer_y), "报告用途：个人健康参考", fill=(0, 0, 0), font=font_body)
        draw.text((1710, footer_y), datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), fill=(0, 0, 0), font=font_body)

        footer_y += 65
        self._draw_centered_text(
            draw,
            page.width,
            footer_y,
            "声明：本报告为人工智能自动生成的健康参考信息，不作为最终诊疗、处方或手术决策依据。",
            font_small,
            (120, 120, 120),
        )

        pages = [page]
        if ai_sections is not None:
            pages.append(self._build_personal_ai_page(ai_sections))

        self._save_pdf_pages(save_path, pages)

    def export_pdf_report(self, save_path, audience="doctor", ai_sections=None):
        audience = (audience or "doctor").strip().lower()
        if audience == "personal":
            self._export_personal_pdf_report(save_path, ai_sections=ai_sections)
        else:
            self._export_doctor_pdf_report(save_path)

