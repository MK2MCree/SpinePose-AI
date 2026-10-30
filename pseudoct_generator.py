import os
from typing import Dict, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import torch
import torch.nn as nn


if hasattr(Image, "Resampling"):
    BICUBIC = Image.Resampling.BICUBIC
    BILINEAR = Image.Resampling.BILINEAR
else:
    BICUBIC = Image.BICUBIC
    BILINEAR = Image.BILINEAR


PSEUDOCT_MODE_TRANSFER = "transfer"
PSEUDOCT_MODE_SOURCE = "source_direct"


def resolve_pseudoct_mode(mode: str) -> str:
    normalized = (mode or PSEUDOCT_MODE_TRANSFER).strip().lower()
    aliases = {
        "transfer": PSEUDOCT_MODE_TRANSFER,
        "rgb": PSEUDOCT_MODE_TRANSFER,
        "real_rgb": PSEUDOCT_MODE_TRANSFER,
        "source": PSEUDOCT_MODE_SOURCE,
        "source_direct": PSEUDOCT_MODE_SOURCE,
        "pix2pix": PSEUDOCT_MODE_SOURCE,
        "pix2pix_left": PSEUDOCT_MODE_SOURCE,
        "left": PSEUDOCT_MODE_SOURCE,
    }
    return aliases.get(normalized, PSEUDOCT_MODE_TRANSFER)


def pseudoct_mode_label(mode: str) -> str:
    resolved = resolve_pseudoct_mode(mode)
    if resolved == PSEUDOCT_MODE_SOURCE:
        return "Pix2Pix左半图直推"
    return "真实RGB迁移模式"


def safe_stem(file_name: str) -> str:
    stem = os.path.splitext(os.path.basename(file_name))[0]
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem).strip("_")
    return cleaned or "image"


class UNetDown(nn.Module):
    def __init__(self, in_size, out_size, normalize=True, dropout=0.0):
        super().__init__()
        layers = [nn.Conv2d(in_size, out_size, 4, 2, 1, bias=False)]
        if normalize:
            layers.append(nn.InstanceNorm2d(out_size))
        layers.append(nn.LeakyReLU(0.2))
        if dropout:
            layers.append(nn.Dropout(dropout))
        self.model = nn.Sequential(*layers)

    def forward(self, x):
        return self.model(x)


class UNetUp(nn.Module):
    def __init__(self, in_size, out_size, dropout=0.0):
        super().__init__()
        layers = [
            nn.ConvTranspose2d(in_size, out_size, 4, 2, 1, bias=False),
            nn.InstanceNorm2d(out_size),
            nn.ReLU(inplace=True),
        ]
        if dropout:
            layers.append(nn.Dropout(dropout))
        self.model = nn.Sequential(*layers)

    def forward(self, x, skip_input):
        x = self.model(x)
        return torch.cat((x, skip_input), 1)


class AnatomyGuidedUNet(nn.Module):
    def __init__(self, in_channels=3, out_channels=3):
        super().__init__()

        self.down1 = UNetDown(in_channels, 64, normalize=False)
        self.down2 = UNetDown(64, 128)
        self.down3 = UNetDown(128, 256)
        self.down4 = UNetDown(256, 512, dropout=0.5)
        self.down5 = UNetDown(512, 512, dropout=0.5)
        self.down6 = UNetDown(512, 512, dropout=0.5)
        self.down7 = UNetDown(512, 512, dropout=0.5)
        self.down8 = UNetDown(512, 512, normalize=False, dropout=0.5)

        self.up1_m = UNetUp(512, 512, dropout=0.5)
        self.up2_m = UNetUp(1024, 512, dropout=0.5)
        self.up3_m = UNetUp(1024, 512, dropout=0.5)
        self.up4_m = UNetUp(1024, 512, dropout=0.5)
        self.up5_m = UNetUp(1024, 256)
        self.up6_m = UNetUp(512, 128)
        self.up7_m = UNetUp(256, 64)
        self.final_m = nn.Sequential(
            nn.Upsample(scale_factor=2),
            nn.ZeroPad2d((1, 0, 1, 0)),
            nn.Conv2d(128, 1, 4, padding=1),
        )

        self.up1_d = UNetUp(512, 512, dropout=0.5)
        self.up2_d = UNetUp(1024, 512, dropout=0.5)
        self.up3_d = UNetUp(1024, 512, dropout=0.5)
        self.up4_d = UNetUp(1024, 512, dropout=0.5)
        self.up5_d = UNetUp(1024, 256)
        self.up6_d = UNetUp(512, 128)
        self.up7_d = UNetUp(256, 64)
        self.final_d = nn.Sequential(
            nn.Upsample(scale_factor=2),
            nn.ZeroPad2d((1, 0, 1, 0)),
            nn.Conv2d(128, out_channels, 4, padding=1),
            nn.Tanh(),
        )

    @staticmethod
    def _apply_guidance(feature_map, mask_feature_map, detach_mask_feature=True):
        if detach_mask_feature:
            mask_feature_map = mask_feature_map.detach()
        attention = torch.sigmoid(mask_feature_map)
        return feature_map * (1.0 + attention)

    def forward(self, x):
        d1 = self.down1(x)
        d2 = self.down2(d1)
        d3 = self.down3(d2)
        d4 = self.down4(d3)
        d5 = self.down5(d4)
        d6 = self.down6(d5)
        d7 = self.down7(d6)
        d8 = self.down8(d7)

        m1_feat = self.up1_m.model(d8)
        u1_m = torch.cat((m1_feat, d7), 1)
        m2_feat = self.up2_m.model(u1_m)
        u2_m = torch.cat((m2_feat, d6), 1)
        m3_feat = self.up3_m.model(u2_m)
        u3_m = torch.cat((m3_feat, d5), 1)
        m4_feat = self.up4_m.model(u3_m)
        u4_m = torch.cat((m4_feat, d4), 1)
        m5_feat = self.up5_m.model(u4_m)
        u5_m = torch.cat((m5_feat, d3), 1)
        m6_feat = self.up6_m.model(u5_m)
        u6_m = torch.cat((m6_feat, d2), 1)
        m7_feat = self.up7_m.model(u6_m)
        u7_m = torch.cat((m7_feat, d1), 1)

        fake_mask_logits = self.final_m(u7_m)
        fake_mask = torch.sigmoid(fake_mask_logits)

        d1_feat = self.up1_d.model(d8)
        u1_d = torch.cat((self._apply_guidance(d1_feat, m1_feat, True), d7), 1)

        d2_feat = self.up2_d.model(u1_d)
        u2_d = torch.cat((self._apply_guidance(d2_feat, m2_feat, True), d6), 1)

        d3_feat = self.up3_d.model(u2_d)
        u3_d = torch.cat((self._apply_guidance(d3_feat, m3_feat, True), d5), 1)

        d4_feat = self.up4_d.model(u3_d)
        u4_d = torch.cat((self._apply_guidance(d4_feat, m4_feat, True), d4), 1)

        d5_feat = self.up5_d.model(u4_d)
        u5_d = torch.cat((self._apply_guidance(d5_feat, m5_feat, False), d3), 1)

        d6_feat = self.up6_d.model(u5_d)
        u6_d = torch.cat((self._apply_guidance(d6_feat, m6_feat, False), d2), 1)

        d7_feat = self.up7_d.model(u6_d)
        u7_d = torch.cat((self._apply_guidance(d7_feat, m7_feat, False), d1), 1)

        fake_B = self.final_d(u7_d)
        return fake_B, fake_mask_logits, fake_mask


def expand_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    image_width: int,
    image_height: int,
    expand_ratio: float,
) -> Tuple[float, float, float, float]:
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    pad_x = width * expand_ratio
    pad_y = height * expand_ratio

    x1 = max(0.0, x1 - pad_x)
    y1 = max(0.0, y1 - pad_y)
    x2 = min(float(image_width), x2 + pad_x)
    y2 = min(float(image_height), y2 + pad_y)
    return x1, y1, x2, y2


def fit_bbox_to_aspect(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    image_width: int,
    image_height: int,
    target_aspect: float,
) -> Tuple[int, int, int, int]:
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    current_aspect = width / height

    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0

    if current_aspect > target_aspect:
        new_width = width
        new_height = new_width / target_aspect
    else:
        new_height = height
        new_width = new_height * target_aspect

    x1 = center_x - new_width / 2.0
    x2 = center_x + new_width / 2.0
    y1 = center_y - new_height / 2.0
    y2 = center_y + new_height / 2.0

    if x1 < 0:
        x2 -= x1
        x1 = 0.0
    if y1 < 0:
        y2 -= y1
        y1 = 0.0
    if x2 > image_width:
        shift = x2 - image_width
        x1 -= shift
        x2 = float(image_width)
    if y2 > image_height:
        shift = y2 - image_height
        y1 -= shift
        y2 = float(image_height)

    x1 = max(0.0, x1)
    y1 = max(0.0, y1)
    x2 = min(float(image_width), x2)
    y2 = min(float(image_height), y2)

    return int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))


def resize_with_letterbox_rgb(
    image: Image.Image,
    target_width: int,
    target_height: int,
    fill_color=(0, 0, 0),
) -> Image.Image:
    image = image.copy().convert("RGB")
    src_width, src_height = image.size
    scale = min(target_width / src_width, target_height / src_height)

    resized_width = max(1, int(round(src_width * scale)))
    resized_height = max(1, int(round(src_height * scale)))

    image = image.resize((resized_width, resized_height), resample=BICUBIC)

    canvas = Image.new("RGB", (target_width, target_height), fill_color)
    offset_x = (target_width - resized_width) // 2
    offset_y = (target_height - resized_height) // 2
    canvas.paste(image, (offset_x, offset_y))
    return canvas


def resize_with_letterbox_mask(
    image: Image.Image,
    target_width: int,
    target_height: int,
    fill_value: int = 0,
) -> Image.Image:
    image = image.copy().convert("L")
    src_width, src_height = image.size
    scale = min(target_width / src_width, target_height / src_height)

    resized_width = max(1, int(round(src_width * scale)))
    resized_height = max(1, int(round(src_height * scale)))

    image = image.resize((resized_width, resized_height), resample=BILINEAR)

    canvas = Image.new("L", (target_width, target_height), fill_value)
    offset_x = (target_width - resized_width) // 2
    offset_y = (target_height - resized_height) // 2
    canvas.paste(image, (offset_x, offset_y))
    return canvas


def create_soft_box_mask(
    size: Tuple[int, int],
    inner_box: Tuple[int, int, int, int],
    outside_value: float = 0.08,
    blur_ratio: float = 0.08,
) -> Image.Image:
    width, height = size
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle(inner_box, fill=255)

    blur_radius = max(1.0, min(width, height) * blur_ratio)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    mask_np = np.asarray(mask, dtype=np.float32) / 255.0
    mask_np = outside_value + (1.0 - outside_value) * mask_np
    mask_np = np.clip(mask_np, 0.0, 1.0)
    return Image.fromarray((mask_np * 255.0).astype(np.uint8), mode="L")


def apply_soft_mask(image: Image.Image, mask: Image.Image) -> Image.Image:
    image_np = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    mask_np = np.asarray(mask.convert("L"), dtype=np.float32) / 255.0
    masked_np = np.clip(image_np * mask_np[..., None], 0.0, 1.0)
    return Image.fromarray((masked_np * 255.0).astype(np.uint8), mode="RGB")


def pil_to_normalized_tensor(image: Image.Image) -> torch.Tensor:
    image_np = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    tensor = torch.from_numpy(image_np).permute(2, 0, 1)
    return tensor * 2.0 - 1.0


def normalized_tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    tensor = tensor.detach().cpu().clamp(-1.0, 1.0)
    array = ((tensor + 1.0) / 2.0).permute(1, 2, 0).numpy()
    array = (array * 255.0).astype(np.uint8)
    return Image.fromarray(array, mode="RGB")


def mask_tensor_to_pil(tensor: torch.Tensor) -> Image.Image:
    if tensor.ndim == 3:
        tensor = tensor[0]
    tensor = tensor.detach().cpu().clamp(0.0, 1.0)
    array = (tensor.numpy() * 255.0).astype(np.uint8)
    return Image.fromarray(array, mode="L").convert("RGB")


def draw_bbox_on_image(image: Image.Image, bbox: Sequence[int], outline=(0, 255, 0), width: int = 8) -> Image.Image:
    out = image.copy().convert("RGB")
    draw = ImageDraw.Draw(out)
    draw.rectangle(list(bbox), outline=outline, width=width)
    return out


def maybe_split_pix2pix_pair(image: Image.Image):
    image = image.copy().convert("RGB")
    ratio = image.width / max(1, image.height)

    if 1.95 <= ratio <= 2.05 and image.width >= 2:
        split_x = image.width // 2
        left = image.crop((0, 0, split_x, image.height)).convert("RGB")
        right = image.crop((split_x, 0, image.width, image.height)).convert("RGB")
        return left, right, "pair_auto_split"

    return image, None, "left_half"


def build_preview_strip(images, tile_height=512) -> Image.Image:
    valid_images = [image for image in images if image is not None]
    resized = []
    total_width = 0

    for image in valid_images:
        ratio = tile_height / image.height
        new_width = int(round(image.width * ratio))
        tile = image.resize((new_width, tile_height), resample=BICUBIC)
        resized.append(tile)
        total_width += new_width

    canvas = Image.new("RGB", (total_width, tile_height), color=(0, 0, 0))
    offset = 0
    for tile in resized:
        canvas.paste(tile, (offset, 0))
        offset += tile.width
    return canvas


class PseudoCTGenerator:
    def __init__(
        self,
        project_root: str,
        output_root: str,
        bbox_detector=None,
        predict_device="cpu",
        image_width: int = 512,
        image_height: int = 768,
        crop_expand_ratio: float = 0.16,
        soft_mask_outside_value: float = 0.08,
        soft_mask_blur_ratio: float = 0.08,
        source_image_width: int = 256,
        source_image_height: int = 256,
        initial_mode: str = PSEUDOCT_MODE_TRANSFER,
    ):
        self.project_root = project_root
        self.output_root = output_root
        self.bbox_detector = bbox_detector
        self.predict_device = predict_device
        self.image_width = image_width
        self.image_height = image_height
        self.crop_expand_ratio = crop_expand_ratio
        self.soft_mask_outside_value = soft_mask_outside_value
        self.soft_mask_blur_ratio = soft_mask_blur_ratio
        self.source_input_size = (source_image_width, source_image_height)
        self.target_aspect = image_width / image_height

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.generator = AnatomyGuidedUNet().to(self.device)

        self.active_mode = None
        self.ready = False
        self.status = "未初始化"
        self.checkpoint_path = None

        os.makedirs(self.output_root, exist_ok=True)
        self.reload(initial_mode)

    def set_bbox_detector(self, detector):
        self.bbox_detector = detector

    def _transfer_candidate_checkpoints(self):
        local_dir = os.path.join(self.project_root, "weights", "pseudoct")
        return [
            os.getenv("MINDSPINE_PSEUDOCT_CKPT_TRANSFER"),
            os.getenv("MINDSPINE_PSEUDOCT_CKPT"),
            os.path.join(local_dir, "best_transfer_v2_generator.pth"),
            os.path.join(local_dir, "best_transfer_generator.pth"),
            os.path.join(local_dir, "best_generator_joint.pth"),
            os.path.join(local_dir, "best_generator.pth"),
            os.path.join(local_dir, "best_generator_ssim.pth"),
        ]

    def _source_candidate_checkpoints(self):
        local_dir = os.path.join(self.project_root, "weights", "pseudoct")
        return [
            os.getenv("MINDSPINE_PSEUDOCT_CKPT_SOURCE"),
            os.getenv("MINDSPINE_PSEUDOCT_CKPT"),
            os.path.join(local_dir, "best_source_generator_joint.pth"),
            os.path.join(local_dir, "best_source_generator.pth"),
            os.path.join(local_dir, "best_generator_joint.pth"),
            os.path.join(local_dir, "best_generator.pth"),
            os.path.join(local_dir, "best_generator_ssim.pth"),
            os.path.join(local_dir, "best_generator_dice.pth"),
            os.path.join(local_dir, "best_pretrain_generator.pth"),
        ]

    def _candidate_checkpoints(self, mode: str):
        resolved_mode = resolve_pseudoct_mode(mode)
        if resolved_mode == PSEUDOCT_MODE_SOURCE:
            return self._source_candidate_checkpoints()
        return self._transfer_candidate_checkpoints()

    def _resolve_checkpoint(self, mode: str) -> Optional[str]:
        for path in self._candidate_checkpoints(mode):
            if path and os.path.exists(path):
                return path
        return None

    @staticmethod
    def _normalize_state_dict(state_dict):
        keys = list(state_dict.keys())
        if not keys:
            return state_dict

        if all(key.startswith("module.") for key in keys):
            state_dict = {key[7:]: value for key, value in state_dict.items()}
            keys = list(state_dict.keys())

        if all(key.startswith("generator.") for key in keys):
            state_dict = {key[10:]: value for key, value in state_dict.items()}

        return state_dict

    def reload(self, mode: str = PSEUDOCT_MODE_TRANSFER):
        resolved_mode = resolve_pseudoct_mode(mode)
        self.active_mode = resolved_mode
        self.ready = False
        self.checkpoint_path = None

        checkpoint_path = self._resolve_checkpoint(resolved_mode)
        if not checkpoint_path:
            if resolved_mode == PSEUDOCT_MODE_SOURCE:
                self.status = "未找到源模型直推权重，请确认 best_source_generator_joint.pth 或 best_generator_joint.pth 已放入项目权重目录。"
            else:
                self.status = "未找到迁移生成权重，请确认 best_transfer_v2_generator.pth 已放入项目权重目录。"
            return

        payload = torch.load(checkpoint_path, map_location=self.device)
        if isinstance(payload, dict) and "generator" in payload:
            state_dict = payload["generator"]
        elif isinstance(payload, dict) and "state_dict" in payload:
            state_dict = payload["state_dict"]
        else:
            state_dict = payload

        state_dict = self._normalize_state_dict(state_dict)
        self.generator.load_state_dict(state_dict, strict=True)
        self.generator.eval()

        self.ready = True
        self.checkpoint_path = checkpoint_path
        ckpt_name = os.path.basename(checkpoint_path)

        if resolved_mode == PSEUDOCT_MODE_SOURCE:
            self.status = f"已加载源模型直推权重：{ckpt_name}"
        elif "transfer" in ckpt_name.lower():
            self.status = f"已加载迁移生成模型：{ckpt_name}"
        else:
            self.status = f"迁移模式未找到专用权重，当前回退到源模型：{ckpt_name}。真实RGB效果可能弱于迁移模型。"

    def ensure_mode(self, mode: str = PSEUDOCT_MODE_TRANSFER) -> str:
        resolved_mode = resolve_pseudoct_mode(mode)
        if resolved_mode != self.active_mode or not self.ready:
            self.reload(resolved_mode)
        return resolved_mode

    def describe(self) -> Dict:
        mode = self.active_mode or PSEUDOCT_MODE_TRANSFER
        if mode == PSEUDOCT_MODE_SOURCE:
            detector = "源模型直推（跳过ROI检测）"
            expected_input = "Pix2Pix左半图，或整张配对图自动切左半"
        else:
            detector = "YOLO 脊柱ROI检测" if self.bbox_detector is not None else "中心框回退"
            expected_input = "真实RGB体表图"

        return {
            "ready": self.ready,
            "status": self.status,
            "mode": mode,
            "mode_label": pseudoct_mode_label(mode),
            "checkpoint_path": "",
            "checkpoint_name": os.path.basename(self.checkpoint_path) if self.checkpoint_path else "未找到",
            "detector": detector,
            "expected_input": expected_input,
            "source_input_size": list(self.source_input_size),
            "transfer_input_size": [self.image_width, self.image_height],
        }

    def detect_bbox(self, image_bgr: np.ndarray) -> Tuple[Tuple[int, int, int, int], float, str]:
        if self.bbox_detector is not None:
            try:
                image_bgr = np.ascontiguousarray(image_bgr)
                results = self.bbox_detector.predict(
                    source=image_bgr,
                    conf=0.35,
                    iou=0.5,
                    device=self.predict_device,
                    verbose=False,
                )
                boxes = results[0].boxes
                best_box = None
                best_conf = 0.0

                if boxes is not None and len(boxes) > 0:
                    for box in boxes:
                        conf = float(box.conf[0].item())
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        if conf > best_conf:
                            best_conf = conf
                            best_box = (int(x1), int(y1), int(x2), int(y2))

                if best_box is not None:
                    return best_box, best_conf, "YOLO"
            except Exception:
                pass

        height, width = image_bgr.shape[:2]
        box_w = max(64, int(width * 0.42))
        box_h = max(128, int(height * 0.78))
        x1 = max(0, (width - box_w) // 2)
        y1 = max(0, int(height * 0.08))
        x2 = min(width, x1 + box_w)
        y2 = min(height, y1 + box_h)
        return (x1, y1, x2, y2), 0.0, "Fallback"

    def _prepare_rgb_views(self, image: Image.Image, bbox: Sequence[int]):
        orig_x1, orig_y1, orig_x2, orig_y2 = map(float, bbox)
        img_w, img_h = image.size

        crop_x1, crop_y1, crop_x2, crop_y2 = expand_bbox(
            orig_x1,
            orig_y1,
            orig_x2,
            orig_y2,
            img_w,
            img_h,
            self.crop_expand_ratio,
        )
        crop_x1, crop_y1, crop_x2, crop_y2 = fit_bbox_to_aspect(
            crop_x1,
            crop_y1,
            crop_x2,
            crop_y2,
            img_w,
            img_h,
            self.target_aspect,
        )

        crop_box = (crop_x1, crop_y1, crop_x2, crop_y2)
        crop = image.crop(crop_box).convert("RGB")

        local_box = (
            int(round(orig_x1 - crop_x1)),
            int(round(orig_y1 - crop_y1)),
            int(round(orig_x2 - crop_x1)),
            int(round(orig_y2 - crop_y1)),
        )
        local_box = (
            max(0, min(crop.width - 1, local_box[0])),
            max(0, min(crop.height - 1, local_box[1])),
            max(1, min(crop.width, local_box[2])),
            max(1, min(crop.height, local_box[3])),
        )

        soft_mask = create_soft_box_mask(
            crop.size,
            inner_box=local_box,
            outside_value=self.soft_mask_outside_value,
            blur_ratio=self.soft_mask_blur_ratio,
        )
        masked_crop = apply_soft_mask(crop, soft_mask)

        raw_resized = resize_with_letterbox_rgb(crop, self.image_width, self.image_height)
        masked_resized = resize_with_letterbox_rgb(masked_crop, self.image_width, self.image_height)
        soft_mask_resized = resize_with_letterbox_mask(soft_mask, self.image_width, self.image_height, fill_value=0)

        return raw_resized, masked_resized, soft_mask_resized, crop_box

    def _prepare_source_views(self, image: Image.Image):
        direct_input, target_image, source_layout = maybe_split_pix2pix_pair(image)

        if source_layout == "pair_auto_split":
            note = "检测到整张 Pix2Pix 配对图，系统已自动截取左半图作为输入。"
        else:
            note = "源模型直推：跳过ROI检测、裁剪和 soft mask，按训练/测试时的输入方式直接推理。"

        model_input = direct_input.copy()
        if model_input.size != self.source_input_size:
            note += f" 输入尺寸为 {model_input.width}x{model_input.height}，已缩放到 {self.source_input_size[0]}x{self.source_input_size[1]}。"
            model_input = model_input.resize(self.source_input_size, resample=BICUBIC)

        return direct_input, model_input, target_image, source_layout, note

    def generate_from_path(
        self,
        image_path: str,
        job_id: str,
        item_index: int = 0,
        inference_mode: str = PSEUDOCT_MODE_TRANSFER,
        output_namespace: str = "",
    ) -> Dict:
        resolved_mode = self.ensure_mode(inference_mode)
        if not self.ready:
            raise RuntimeError(self.status)

        if resolved_mode == PSEUDOCT_MODE_SOURCE:
            return self._generate_source_from_path(image_path, job_id, item_index, output_namespace)
        return self._generate_transfer_from_path(image_path, job_id, item_index, output_namespace)

    def _generate_transfer_from_path(
        self,
        image_path: str,
        job_id: str,
        item_index: int = 0,
        output_namespace: str = "",
    ) -> Dict:
        with Image.open(image_path) as image_obj:
            rgb_image = image_obj.convert("RGB")

        image_bgr = cv2.cvtColor(np.asarray(rgb_image), cv2.COLOR_RGB2BGR)
        bbox, bbox_confidence, detector_name = self.detect_bbox(image_bgr)

        overlay_preview = resize_with_letterbox_rgb(
            draw_bbox_on_image(rgb_image, bbox),
            self.image_width,
            self.image_height,
        )
        raw_crop, masked_input, soft_mask, crop_box = self._prepare_rgb_views(rgb_image, bbox)

        input_tensor = pil_to_normalized_tensor(masked_input).unsqueeze(0).to(self.device)

        with torch.no_grad():
            fake_ct, _, fake_mask = self.generator(input_tensor)

        generated_image = normalized_tensor_to_pil(fake_ct[0])
        pred_mask_image = mask_tensor_to_pil(fake_mask[0])

        preview_strip = build_preview_strip(
            [overlay_preview, raw_crop, masked_input, generated_image, pred_mask_image],
            tile_height=512,
        )

        case_id = f"case_{item_index:03d}_{safe_stem(image_path)}"
        case_dir = os.path.join(self.output_root, output_namespace, job_id, case_id)
        os.makedirs(case_dir, exist_ok=True)

        paths = {
            "overlay": os.path.join(case_dir, "overlay.png"),
            "crop": os.path.join(case_dir, "crop.png"),
            "masked": os.path.join(case_dir, "masked_input.png"),
            "soft_mask": os.path.join(case_dir, "soft_mask.png"),
            "generated": os.path.join(case_dir, "xray.png"),
            "mask": os.path.join(case_dir, "pred_mask.png"),
            "preview": os.path.join(case_dir, "preview_strip.png"),
        }

        overlay_preview.save(paths["overlay"])
        raw_crop.save(paths["crop"])
        masked_input.save(paths["masked"])
        soft_mask.convert("RGB").save(paths["soft_mask"])
        generated_image.save(paths["generated"])
        pred_mask_image.save(paths["mask"])
        preview_strip.save(paths["preview"])

        mask_fill_ratio = float((fake_mask[0] > 0.5).float().mean().item())

        return {
            "job_id": job_id,
            "case_id": case_id,
            "source_name": os.path.basename(image_path),
            "inference_mode": PSEUDOCT_MODE_TRANSFER,
            "mode_label": pseudoct_mode_label(PSEUDOCT_MODE_TRANSFER),
            "source_layout": "roi_crop_soft_mask",
            "note": "迁移模式：先做ROI检测、外扩裁剪、长宽比拟合和 soft mask，再送入生成器。",
            "bbox": [int(v) for v in bbox],
            "crop_box": [int(v) for v in crop_box],
            "bbox_confidence": float(bbox_confidence),
            "mask_fill_ratio": mask_fill_ratio,
            "detector_name": detector_name,
            "checkpoint_name": os.path.basename(self.checkpoint_path) if self.checkpoint_path else "未找到",
            "image_size": [int(rgb_image.width), int(rgb_image.height)],
            "network_input_size": [int(masked_input.width), int(masked_input.height)],
            "paths": paths,
        }

    def _generate_source_from_path(
        self,
        image_path: str,
        job_id: str,
        item_index: int = 0,
        output_namespace: str = "",
    ) -> Dict:
        with Image.open(image_path) as image_obj:
            source_image = image_obj.convert("RGB")

        direct_input, model_input, target_image, source_layout, note = self._prepare_source_views(source_image)
        input_tensor = pil_to_normalized_tensor(model_input).unsqueeze(0).to(self.device)

        with torch.no_grad():
            fake_ct, _, fake_mask = self.generator(input_tensor)

        generated_image = normalized_tensor_to_pil(fake_ct[0])
        pred_mask_image = mask_tensor_to_pil(fake_mask[0])

        preview_images = [direct_input, model_input, generated_image]
        if target_image is not None:
            preview_images.append(target_image)
        preview_images.append(pred_mask_image)
        preview_strip = build_preview_strip(preview_images, tile_height=512)

        case_id = f"case_{item_index:03d}_{safe_stem(image_path)}"
        case_dir = os.path.join(self.output_root, output_namespace, job_id, case_id)
        os.makedirs(case_dir, exist_ok=True)

        target_or_input = target_image if target_image is not None else direct_input

        paths = {
            "overlay": os.path.join(case_dir, "source_input.png"),
            "crop": os.path.join(case_dir, "model_input.png"),
            "masked": os.path.join(case_dir, "model_input_copy.png"),
            "soft_mask": os.path.join(case_dir, "target_or_reference.png"),
            "generated": os.path.join(case_dir, "xray.png"),
            "mask": os.path.join(case_dir, "pred_mask.png"),
            "preview": os.path.join(case_dir, "preview_strip.png"),
        }

        direct_input.save(paths["overlay"])
        model_input.save(paths["crop"])
        model_input.save(paths["masked"])
        target_or_input.save(paths["soft_mask"])
        generated_image.save(paths["generated"])
        pred_mask_image.save(paths["mask"])
        preview_strip.save(paths["preview"])

        mask_fill_ratio = float((fake_mask[0] > 0.5).float().mean().item())

        return {
            "job_id": job_id,
            "case_id": case_id,
            "source_name": os.path.basename(image_path),
            "inference_mode": PSEUDOCT_MODE_SOURCE,
            "mode_label": pseudoct_mode_label(PSEUDOCT_MODE_SOURCE),
            "source_layout": source_layout,
            "note": note,
            "bbox": [],
            "crop_box": [],
            "bbox_confidence": None,
            "mask_fill_ratio": mask_fill_ratio,
            "detector_name": "SourceDirect",
            "checkpoint_name": os.path.basename(self.checkpoint_path) if self.checkpoint_path else "未找到",
            "image_size": [int(source_image.width), int(source_image.height)],
            "network_input_size": [int(model_input.width), int(model_input.height)],
            "paths": paths,
        }
