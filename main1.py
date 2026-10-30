# main1.py
import os
import io
import base64
import cv2
import eel
import numpy as np
import tkinter as tk
import time
from tkinter import filedialog
from model import SpineModel  # 确保 model.py 在同级目录

# 初始化 AI 引擎
model = SpineModel()

# 启动 Eel 引擎指向本地 web 目录
eel.init('web')

# 隐藏的主 Tkinter Root（专用于调用原生操作系统的文件选择器）
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)

# ========== 用于存储分片上传的内存缓冲池 ==========
file_chunks_buffer = {}


def cv2_to_base64(img):
    if img is None: return ""
    _, buffer = cv2.imencode('.jpg', img)
    return "data:image/jpeg;base64," + base64.b64encode(buffer).decode('utf-8')


def push_ui_update():
    """统一下发图像和指标数据到前端的快捷函数"""
    main_b64 = cv2_to_base64(model.current_main_img)
    sub_b64 = cv2_to_base64(model.current_sub_img)
    eel.updateDisplay(main_b64, sub_b64)()
    eel.updateMetrics(model.current_angles, model.current_slip_list)()


@eel.expose
def switch_mode(mode_id):
    model.current_mode = mode_id
    model.current_cv_img = None
    model.raw_pixel_array = None
    model.is_batch_mode = False
    model.current_main_img = None
    model.current_sub_img = None
    model.current_angles = None
    model.current_slip_list = []
    eel.setStatus(f"系统切换至: {mode_id.upper()}", "#F4F5F8")()


@eel.expose
def import_data(mode, is_batch):
    if is_batch:
        path = filedialog.askdirectory()
        if not path: return

        valid_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.dcm')
        model.image_list = [os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(valid_extensions)]

        if not model.image_list:
            eel.setStatus("未发现有效图像文件", "#EF4444")()
            return

        model.is_batch_mode = True
        model.folder_path = path
        eel.setStatus(f"队列就绪: {len(model.image_list)} 个文件", "#F4F5F8")()

    else:
        path = filedialog.askopenfilename()
        if not path: return

        model.is_batch_mode = False
        model.image_path = path

        cv_img, auto_scale, raw_arr, ww, wl = model.read_medical_image_and_scale(path, 1.5)
        if cv_img is None:
            eel.setStatus("图像读取失败", "#EF4444")()
            return

        cv_img, raw_arr = model.auto_flip_if_needed(cv_img, model.current_mode, raw_arr)

        model.current_cv_img = cv_img
        model.raw_pixel_array = raw_arr
        model.current_main_img = cv_img
        model.current_sub_img = None
        model.cv_image_result = None
        model.last_bbox = None
        model.current_angles = None
        model.current_slip_list = []

        if raw_arr is not None and ww is not None and wl is not None:
            min_v, max_v = np.min(raw_arr), np.max(raw_arr)
            eel.updateDICOMUI(wl, ww, float(min_v), float(max_v), auto_scale)()
        elif auto_scale is not None:
            eel.updateScaleInput(auto_scale)()

        push_ui_update()
        eel.setStatus("图像加载成功", "#22C55E")()


@eel.expose
def apply_windowing(wl, ww):
    if model.raw_pixel_array is None: return
    model.update_windowing(wl, ww)
    push_ui_update()


@eel.expose
def run_xray(scale):
    if model.model_xray is None:
        eel.setStatus("错误: 未找到 X 光模型权重", "#EF4444")()
        return

    if model.current_cv_img is None and not model.is_batch_mode:
        eel.setStatus("请先导入图像", "#EF4444")()
        return

    def process():
        try:
            if model.is_batch_mode:
                eel.setStatus("批量处理中...", "#5E6AD2")()
                eel.sleep(0.05)  # 关键修复：主动让出 CPU 协程控制权，让 WebSocket 把文字发给前端

                out_dir = os.path.join(model.folder_path, "MindSpine_Export")
                os.makedirs(out_dir, exist_ok=True)
                total = len(model.image_list)

                for i, p in enumerate(model.image_list):
                    cv_img, fs, _, _, _ = model.read_medical_image_and_scale(p, scale)
                    if cv_img is None: continue

                    cv_img, _ = model.auto_flip_if_needed(cv_img, "xray")
                    res = model.model_xray.predict(source=cv_img, conf=0.5, device=model.run_device, verbose=False)
                    ann_frame = res[0].plot(boxes=False, labels=False)
                    ann_frame, angles, slips = model.process_medical_image(res[0], ann_frame, fs)

                    model.current_main_img = ann_frame
                    model.current_angles = angles
                    model.current_slip_list = slips
                    push_ui_update()
                    eel.setStatus(f"进度: {i + 1}/{total}", "#5E6AD2")()

                    # 关键修复：批处理循环中必须释放一下线程，否则长连接容易被判超时导致闪退
                    eel.sleep(0.01)

                    sv = os.path.join(out_dir, os.path.basename(p))
                    if sv.lower().endswith('.dcm'): sv = sv[:-4] + '.jpg'
                    cv2.imwrite(sv, ann_frame)

                eel.setStatus(f"处理完成，已导出至: {out_dir}", "#22C55E")()

            else:
                eel.setStatus("正在测算脊柱指标...", "#5E6AD2")()
                eel.sleep(0.05)  # 关键修复：等待 UI 渲染完成再锁定 CPU 计算

                res = model.model_xray.predict(source=model.current_cv_img, conf=0.5, device=model.run_device,
                                               verbose=False)
                ann_frame = res[0].plot(boxes=False, labels=False)
                ann_frame, angles, slips = model.process_medical_image(res[0], ann_frame, scale)

                model.cv_image_result = ann_frame
                model.current_main_img = ann_frame
                model.current_sub_img = model.current_cv_img
                model.current_angles = angles
                model.current_slip_list = slips

                push_ui_update()
                eel.setStatus("分析完成", "#22C55E")()

        except Exception as e:
            print(f"X 光分析异常: {e}")
            eel.setStatus(f"分析异常: {str(e)}", "#EF4444")()

    # 关键修复：使用 eel.spawn 创建安全协程代替 threading.Thread
    eel.spawn(process)


@eel.expose
def run_rgb():
    if model.model_rgb is None:
        eel.setStatus("错误：RGB 模型未加载", "#EF4444")()
        return

    if model.current_cv_img is None and not model.is_batch_mode:
        eel.setStatus("请先导入图像", "#EF4444")()
        return

    def process():
        try:
            if model.is_batch_mode:
                eel.setStatus("批量侦测中...", "#5E6AD2")()
                eel.sleep(0.05)  # 释放协程以刷新 UI

                out_dir = os.path.join(model.folder_path, "RGB_Annotated")
                os.makedirs(out_dir, exist_ok=True)
                total = len(model.image_list)

                for i, p in enumerate(model.image_list):
                    img = cv2.imread(p)
                    if img is None: continue

                    img, _ = model.auto_flip_if_needed(img, "rgb")
                    res = model.model_rgb.predict(source=img, conf=0.45, iou=0.5, device=model.run_device,
                                                  verbose=False)
                    boxes = res[0].boxes

                    if len(boxes) > 0:
                        best_box, max_c = None, -1.0
                        for box in boxes:
                            if box.conf[0].item() > max_c:
                                max_c = float(box.conf[0].item())
                                best_box = tuple(map(int, box.xyxy[0].cpu().numpy()))

                        if best_box:
                            x1, y1, x2, y2 = best_box
                            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            label = f"Spine {max_c * 100:.1f}%"
                            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                            cv2.rectangle(img, (x1, y1 - 25), (x1 + w, y1), (0, 255, 0), -1)
                            cv2.putText(img, label, (x1, y1 - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                    model.current_main_img = img
                    push_ui_update()
                    eel.setStatus(f"进度: {i + 1}/{total}", "#5E6AD2")()
                    eel.sleep(0.01)  # 循环休眠保活长连接

                    cv2.imwrite(os.path.join(out_dir, os.path.basename(p)), img)

                eel.updateRGBMetrics(None)()
                eel.setStatus("批量侦测完成", "#22C55E")()

            else:
                eel.setStatus("正在侦测目标...", "#5E6AD2")()
                eel.sleep(0.05)  # 必须在这里加上 sleep 刷新UI，否则 PyTorch 立刻锁死导致掉线闪退

                img = model.current_cv_img.copy()

                if len(img.shape) == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                elif len(img.shape) == 3 and img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                img = np.ascontiguousarray(img)
                res = model.model_rgb.predict(source=img, conf=0.45, iou=0.5, device=model.run_device, verbose=False)
                boxes = res[0].boxes

                best_box, max_c = None, -1.0
                if len(boxes) > 0:
                    for box in boxes:
                        if box.conf[0].item() > max_c:
                            max_c = float(box.conf[0].item())
                            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                            best_box = (int(x1), int(y1), int(x2), int(y2))

                    if best_box:
                        model.last_bbox = best_box
                        x1, y1, x2, y2 = best_box
                        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        label = f"Spine {max_c * 100:.1f}%"
                        (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                        cv2.rectangle(img, (x1, y1 - 25), (x1 + w, y1), (0, 255, 0), -1)
                        cv2.putText(img, label, (x1, y1 - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                model.cv_image_result = img
                model.current_main_img = img
                model.current_sub_img = model.current_cv_img
                push_ui_update()

                if best_box:
                    eel.updateRGBMetrics({"conf": max_c, "box": list(best_box)})()
                else:
                    eel.updateRGBMetrics(None)()

                eel.setStatus("侦测完成", "#22C55E")()

        except Exception as e:
            print(f"RGB 侦测出错: {e}")
            eel.setStatus(f"RGB 侦测出错: {str(e)}", "#EF4444")()

    # 关键修复：使用 eel.spawn() 生成纯血的 Gevent 协程任务
    eel.spawn(process)


@eel.expose
def receive_file_chunk(file_id, chunk_data, chunk_index, total_chunks, filename, mode):
    """接收前端分片传来的文件数据，防止超大图像把 WebSocket 撑爆"""
    try:
        if file_id not in file_chunks_buffer:
            file_chunks_buffer[file_id] = []

        file_chunks_buffer[file_id].append((chunk_index, chunk_data))

        # 当收集齐所有的分片后，进行合并处理
        if len(file_chunks_buffer[file_id]) == total_chunks:
            eel.setStatus("图像合并与解析中...", "#5E6AD2")()
            eel.sleep(0.05)

            # 严格按照 chunk_index 进行升序排序，确保数据不出错
            file_chunks_buffer[file_id].sort(key=lambda x: x[0])
            full_base64 = "".join([c[1] for c in file_chunks_buffer[file_id]])
            del file_chunks_buffer[file_id]

            # 调用原本的图像处理引擎
            process_dropped_file(full_base64, filename, mode)

    except Exception as e:
        print(f"分片合并失败: {e}")
        eel.setStatus("文件传输失败，请重试", "#EF4444")()


@eel.expose
def process_dropped_file(base64_data, filename, mode):
    """处理拖拽上传的合并后的完整 Base64 图像"""
    try:
        if "," in base64_data:
            _, base64_str = base64_data.split(',', 1)
        else:
            base64_str = base64_data

        temp_dir = os.path.join(os.getcwd(), "temp_uploads")
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.join(temp_dir, filename)

        with open(file_path, "wb") as f:
            f.write(base64.b64decode(base64_str))

        if model.current_mode != mode:
            switch_mode(mode)

        model.is_batch_mode = False
        model.image_path = file_path

        cv_img, auto_scale, raw_arr, ww, wl = model.read_medical_image_and_scale(file_path, 1.5)
        if cv_img is None:
            eel.setStatus("读取失败", "#EF4444")()
            return

        cv_img, raw_arr = model.auto_flip_if_needed(cv_img, model.current_mode, raw_arr)
        model.current_cv_img = cv_img
        model.raw_pixel_array = raw_arr
        model.current_main_img = cv_img
        model.current_sub_img = None

        if raw_arr is not None and ww is not None:
            min_v, max_v = np.min(raw_arr), np.max(raw_arr)
            eel.updateDICOMUI(wl, ww, float(min_v), float(max_v), auto_scale)()
        elif auto_scale:
            eel.updateScaleInput(auto_scale)()

        push_ui_update()
        eel.setStatus("拖拽影像加载成功", "#22C55E")()

    except Exception as e:
        print(f"拖拽解析失败: {e}")
        eel.setStatus("拖拽解析失败", "#EF4444")()


@eel.expose
def crop_rgb():
    if model.last_bbox is None or model.is_batch_mode: return
    model.crop_rgb_image()
    push_ui_update()
    eel.setStatus("Cropped successfully.", "#F4F5F8")()


@eel.expose
def mirror_image():
    model.mirror_image()
    push_ui_update()


@eel.expose
def clear_canvas():
    model.cv_image_result = None
    model.current_main_img = model.current_cv_img
    model.current_sub_img = None
    model.current_angles = None
    model.current_slip_list = []


@eel.expose
def save_image():
    if model.current_main_img is None: return
    p = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPEG", "*.jpg")])
    if p: cv2.imwrite(p, model.current_main_img)


@eel.expose
def export_pdf():
    if model.current_mode != "xray" or not model.current_angles:
        eel.setStatus("请先执行智能分析", "#EF4444")()
        return
    p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
    if p:
        model.export_pdf_report(p)
        eel.setStatus("报告导出成功", "#22C55E")()
        if os.name == 'nt': os.startfile(p)


@eel.expose
def swap_screens():
    if model.current_sub_img is None: return
    model.current_main_img, model.current_sub_img = model.current_sub_img, model.current_main_img
    push_ui_update()


if __name__ == '__main__':
    status_msg = model.load_models()
    print(f"Server Startup: {status_msg}")


    def close_callback(page, sockets):
        print(f"\n[{page}] 触发断开连接判定...")
        eel.sleep(2.0)
        if not eel._websockets:
            print("前端界面已完全关闭，后台服务即将正常退出 (os._exit(0))。")
            os._exit(0)
        else:
            print("前端界面仍处于连接状态，取消退出。")


    start_kwargs = {
        'size': (1400, 880),
        'port': 0,
        'close_callback': close_callback
    }

    try:
        eel.start('index1.html', mode='chrome', **start_kwargs)
    except OSError:
        try:
            eel.start('index1.html', mode='edge', **start_kwargs)
        except OSError:
            eel.start('index1.html', mode='default', **start_kwargs)