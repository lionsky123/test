# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "numpy==2.2.6",
#   "opencv-python-headless==4.12.0.88",
#   "pillow>=11.3,<13",
#   "imageio-ffmpeg==0.6.0",
#   "onnxruntime==1.22.1",
#   "torch>=2.7,<2.9",
#   "transformers==4.55.4",
#   "huggingface-hub==0.34.6",
# ]
# [tool.uv.sources]
# torch = { index = "torch-cu128", marker = "sys_platform == 'win32' and platform_machine == 'AMD64'" }
# [[tool.uv.index]]
# name = "torch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///
r"""本地普通视频转单目 360° 全景。

运行：uv run C:\Users\1\Downloads\video_to_360.py "C:\素材\视频.mp4"
须预先安装本地 ComfyUI 和 FLUX 全景模型。BLIP 和 U²-Net 缓存后可用 --offline。
只传视频路径时，输出到用户的 Downloads 文件夹，文件名为 <原文件名>_360.mp4。
每个镜头保留已拍到的高清背景，由本地模型补全其余全景，再逐帧叠加透明主体。
默认用 U²-Net 提取前景。固定主体区域的素材可选用框选 SAM ViT-B：
--sam-box 508 317 825 647 --sam-checkpoint "C:\模型\sam_vit_b_01ec64.pth"
框坐标以视频正常显示方向的原始像素计，右边界和下边界不包含在框内。
请在运行脚本的 Python 环境安装 torchvision 和官方 segment-anything。
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener, urlopen
import uuid
import warnings

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
CAPTION_MODEL = "Salesforce/blip-image-captioning-base"
U2NET_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
U2NET_MIRROR = "https://huggingface.co/edgetools/u2netp/resolve/main/u2netp.onnx"
U2NET_MD5 = "8e83ca70e441ab06c318d82300c84806"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "video-to-360"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="本机视频文件路径")
    parser.add_argument("--output", type=Path, help="指定 MP4 保存路径")
    parser.add_argument("--start", type=float, default=0.0, help="从指定秒数开始")
    parser.add_argument("--duration", type=float, help="只转换指定时长")
    parser.add_argument("--width", type=int, default=3840, help="全景宽度，须为 4 的倍数")
    parser.add_argument("--fps", type=int, default=24, help="输出帧率，默认 24")
    parser.add_argument("--seed", type=int, default=360, help="生成背景随机种子")
    parser.add_argument("--offline", action="store_true", help="只使用已缓存的模型")
    parser.add_argument(
        "--sam-box", type=int, nargs=4, metavar=("X1", "Y1", "X2", "Y2"),
        help="改用框选 SAM ViT-B 逐帧提取前景，使用原视频像素坐标",
    )
    parser.add_argument("--sam-checkpoint", type=Path, help="本地 SAM ViT-B 权重文件")
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"视频文件不存在：{args.video}")
    if not math.isfinite(args.start) or args.start < 0:
        parser.error("--start 必须是非负数")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration 必须大于 0")
    if args.width < 256 or args.width % 4 or not 1 <= args.fps <= 120:
        parser.error("--width 须至少为 256 且为 4 的倍数，--fps 须为 1 至 120")
    if (args.sam_box is None) != (args.sam_checkpoint is None):
        parser.error("--sam-box 和 --sam-checkpoint 须同时提供")
    if args.sam_box is not None:
        x1, y1, x2, y2 = args.sam_box
        if not (0 <= x1 < x2 and 0 <= y1 < y2):
            parser.error("--sam-box 须满足 0 ≤ X1 < X2 且 0 ≤ Y1 < Y2")
        if not args.sam_checkpoint.is_file():
            parser.error(f"SAM ViT-B 权重文件不存在：{args.sam_checkpoint}")
        args.sam_checkpoint = args.sam_checkpoint.resolve()
    args.video = args.video.resolve()
    if args.output is None:
        args.output = Path.home() / "Downloads" / f"{args.video.stem}_360.mp4"
    args.output = args.output.resolve()
    if args.output.suffix.lower() != ".mp4" or args.output == args.video:
        parser.error("输出须为单独的 MP4 文件")
    if args.output.exists():
        parser.error(f"输出已存在，请指定其他名称：{args.output}")
    return args


def read_frames(args, max_side):
    filters = (
        f"fps={args.fps}:start_time=0,"
        f"scale='min({max_side},iw)':'min({max_side},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
    )
    input_options = ["-ss", str(args.start)] if args.start else []
    output_options = ["-map", "0:v:0", "-vf", filters, "-filter_threads", "2", "-threads:v", "2"]
    if args.duration is not None:
        output_options += ["-t", str(args.duration)]
    stream = imageio_ffmpeg.read_frames(
        str(args.video), input_params=input_options, output_params=output_options,
    )
    try:
        width, height = next(stream)["size"]
        for data in stream:
            yield np.frombuffer(data, np.uint8).reshape(height, width, 3)
    finally:
        stream.close()


def image_signature(frame):
    small = cv2.resize(frame, (96, 54), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [16, 6, 8], [0, 180, 0, 256, 0, 256])
    return small, histogram / histogram.sum()


def find_shots(args):
    starts = []
    samples = []
    previous = None
    sample_interval = max(1, args.fps // 3)
    count = 0
    for index, frame in enumerate(read_frames(args, 640)):
        signature = image_signature(frame)
        if previous is None:
            starts.append(0)
            samples.append([])
        elif index - starts[-1] >= max(2, args.fps // 3):
            pixel_shift = float(cv2.absdiff(previous[0], signature[0]).mean())
            histogram_shift = cv2.compareHist(previous[1], signature[1], cv2.HISTCMP_BHATTACHARYYA)
            if (pixel_shift > 25 and histogram_shift > 0.36) or pixel_shift > 53:
                starts.append(index)
                samples.append([])
                sample_interval = max(1, args.fps // 3)
        previous = signature
        if (index - starts[-1]) % sample_interval == 0:
            samples[-1].append(index)
            if len(samples[-1]) > 18:
                samples[-1] = samples[-1][::2]
                sample_interval *= 2
        count = index + 1
    if not starts:
        raise RuntimeError("指定时间范围内未找到可解码的视频帧")
    print(f"检测到 {len(starts)} 个镜头，共 {count} 帧。", flush=True)
    return starts, samples, count


class ForegroundModel:
    def __init__(self, offline):
        import onnxruntime as ort

        path = CACHE / "u2netp.onnx"
        valid = path.is_file() and self._digest(path) == U2NET_MD5
        if not valid:
            if offline:
                raise RuntimeError(f"离线模式缺少模型：{path}")
            print("下载 U²-Net 前景模型……", flush=True)
            CACHE.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as temporary:
                temp_path = Path(temporary.name)
            try:
                for url in (U2NET_URL, U2NET_MIRROR):
                    try:
                        with urlopen(url, timeout=30) as source, temp_path.open("wb") as target:
                            shutil.copyfileobj(source, target)
                        if self._digest(temp_path) == U2NET_MD5:
                            break
                    except OSError:
                        continue
                else:
                    raise RuntimeError("U²-Net 模型下载或校验失败")
                temp_path.replace(path)
            finally:
                temp_path.unlink(missing_ok=True)

        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, os.cpu_count() or 1)
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "md5").hexdigest()

    def mask(self, frame):
        width, height = frame.shape[1], frame.shape[0]
        small = cv2.resize(frame, (320, 320), interpolation=cv2.INTER_AREA).astype(np.float32)
        small /= max(float(small.max()), 1)
        small -= np.array([0.485, 0.456, 0.406], np.float32)
        small /= np.array([0.229, 0.224, 0.225], np.float32)
        data = np.ascontiguousarray(small.transpose(2, 0, 1)[None])
        prediction = self.session.run([self.output_name], {self.input_name: data})[0].squeeze()
        low, high = float(prediction.min()), float(prediction.max())
        if high < 0.35 or high - low < 0.15:
            return np.zeros((height, width), np.float32)
        alpha = np.clip((prediction - max(low, 0.20)) / max(high - max(low, 0.20), 0.15), 0, 1)
        return cv2.resize(alpha, (width, height), interpolation=cv2.INTER_LINEAR)


def collect_shot_samples(stream, indexes):
    frames = []
    wanted = iter(indexes)
    next_index = next(wanted, None)
    for index, frame in stream:
        if index == next_index:
            frames.append(frame)
            next_index = next(wanted, None)
            if next_index is None:
                return frames
    raise RuntimeError("无法定位镜头中的高清背景参考帧")


def clean_background(frames, foreground, *, matte_for_sample=None):
    """Keep the reference's observed pixels; use the nearest aligned donors for occlusions."""
    reference_index = len(frames) // 2
    reference = frames[reference_index]
    height, width = reference.shape[:2]
    scale = min(1.0, 640 / max(width, height))
    proxy_size = (max(2, round(width * scale)), max(2, round(height * scale)))
    window = cv2.createHanningWindow(proxy_size, cv2.CV_32F)

    def alignment_image(frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        return cv2.resize(gray, proxy_size, interpolation=cv2.INTER_AREA).astype(np.float32) * window

    reference_window = alignment_image(reference)
    aligned = []
    measured = []
    for index in sorted(range(len(frames)), key=lambda i: abs(i - reference_index)):
        frame = frames[index]
        if matte_for_sample is None:
            matte = refine_matte(foreground.mask(frame))
        else:
            matte = matte_for_sample(index, frame)
        subject = matte > 0.24
        available = np.ones((height, width), np.uint8)
        if subject.any():
            x, y, w, h = cv2.boundingRect(subject.astype(np.uint8))
            pad_x = max(round(width * 0.019), round(w * 0.08))
            pad_y = max(round(height * 0.033), round(h * 0.13))
            available[max(0, y - pad_y):y + h + pad_y, max(0, x - pad_x):x + w + pad_x] = 0
        if index != reference_index:
            (dx, dy), response = cv2.phaseCorrelate(reference_window, alignment_image(frame))
            dx *= width / proxy_size[0]
            dy *= height / proxy_size[1]
            if not math.isfinite(response) or not math.isfinite(dx + dy):
                continue
            if response < 0.17 or abs(dx) > width * 0.14 or abs(dy) > height * 0.14:
                continue
            offset = np.float32([[1, 0, -dx], [0, 1, -dy]])
            frame = cv2.warpAffine(frame, offset, (width, height), flags=cv2.INTER_CUBIC)
            available = cv2.warpAffine(available, offset, (width, height), flags=cv2.INTER_NEAREST)
        aligned.append(frame)
        measured.append(available != 0)

    plate = reference.copy()
    holes = np.zeros((height, width), np.uint8)
    required = max(1, math.ceil(len(aligned) * 0.25))
    # Use the temporal consensus to reject feathers missed by the semantic mask.
    # Choose an actual recorded sample for each known pixel to keep its texture intact.
    for top in range(0, height, 64):
        rows = slice(top, top + 64)
        valid = np.stack([mask[rows] for mask in measured])
        pixels = np.stack([frame[rows] for frame in aligned])
        observed = pixels.astype(np.float32)
        observed[~valid] = np.nan
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="All-NaN slice encountered")
            median = np.nanmedian(observed, axis=0)
        difference = np.max(np.abs(observed - median), axis=3)
        agreeing = valid & (difference <= 14)
        # Camera alignment can move high-contrast moss by a fraction of a pixel across samples.
        # Keep a clear reference outside the subject's observed footprint when its color is consistent.
        clear_reference = valid.all(axis=0) & (difference[0] <= 23)
        supported = (agreeing.sum(axis=0) >= required) | clear_reference
        holes[rows] = ~supported
        chosen = np.argmax(agreeing, axis=0)
        chosen[clear_reference] = 0
        recorded = np.take_along_axis(pixels, chosen[None, ..., None], axis=0)[0]
        plate[rows][supported] = recorded[supported]
    edge_size = max(3, 2 * round(max(width, height) / 160) + 1)
    holes = cv2.dilate(holes, np.ones((edge_size, edge_size), np.uint8))
    if holes.mean() > 0.88:
        holes[[0, -1], :] = 0
        holes[:, [0, -1]] = 0
    # Preliminary fill supplies context to the captioner. These pixels stay masked for FLUX and compositing.
    if holes.any():
        plate = cv2.inpaint(plate, holes, 5, cv2.INPAINT_TELEA)
    return plate, (holes == 0).astype(np.float32)



class FrontProjection:
    """Longitude/latitude rays mapped to the source camera's perspective plane."""

    def __init__(self, width, height, source_size, horizontal_fov):
        sw, sh = source_size
        half_h = math.radians(horizontal_fov / 2)
        tan_h = math.tan(half_h)
        tan_v = min(tan_h * sh / sw, math.tan(math.radians(76)))
        half_v = math.atan(tan_v)
        margin_x = math.ceil(width * half_h / math.tau) + 1
        margin_y = math.ceil(height * half_v / math.pi) + 1
        x0, x1 = max(0, width // 2 - margin_x), min(width, width // 2 + margin_x)
        y0, y1 = max(0, height // 2 - margin_y), min(height, height // 2 + margin_y)
        self.region = np.s_[y0:y1, x0:x1]
        lon = (np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5) / width * math.tau - math.pi
        lat = math.pi / 2 - (np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5) / height * math.pi
        nx = np.broadcast_to(np.tan(lon) / tan_h, (y1 - y0, x1 - x0))
        ny = np.tan(lat) / (np.cos(lon) * tan_v)
        self.x = ((nx + 1.0) * (sw - 1) / 2).astype(np.float32)
        self.y = ((1.0 - ny) * (sh - 1) / 2).astype(np.float32)
        edge = np.clip(1.0 - np.maximum(np.abs(nx), np.abs(ny)), 0, 1)
        self.coverage = edge > 0

    def sample(self, pixels):
        return cv2.remap(pixels, self.x, self.y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def preserve_recorded_background(panorama, plate, confidence, fov, size):
    """Composite only reliable background, blending coarse tone and fine texture separately."""
    width, height = size
    background = cv2.resize(panorama, size, interpolation=cv2.INTER_CUBIC)
    projection = FrontProjection(width, height, plate.shape[1::-1], fov)
    known = projection.coverage & (projection.sample(confidence) > 0.995)
    if not known.any():
        return background

    region = projection.region
    recorded = projection.sample(plate).astype(np.float32)
    generated = background[region].astype(np.float32)
    measured = known.astype(np.float32)
    distance = cv2.distanceTransform(known.astype(np.uint8), cv2.DIST_L2, 5)

    # Broad exposure transitions hide the boundary. The narrower texture transition keeps bark and moss crisp.
    scale = width / 3840
    sigma = max(0.75, 10.0 * scale)
    weight = cv2.GaussianBlur(measured, (0, 0), sigma)
    recorded_low = cv2.GaussianBlur(recorded * measured[..., None], (0, 0), sigma)
    recorded_low /= np.maximum(weight[..., None], 1e-5)
    generated_low = cv2.GaussianBlur(generated, (0, 0), sigma)
    tone = smoothstep(distance / (148.0 * scale))[..., None]
    detail = smoothstep(distance / (38.0 * scale))[..., None]
    low_difference = recorded_low - generated_low
    mixed = generated + tone * low_difference + detail * (recorded - generated - low_difference)
    background[region] = np.rint(np.clip(mixed, 0, 255)).astype(np.uint8)
    return background



def harmonize_palette(environment, plate, confidence, known):
    """Match the generated surroundings to the lighting of the recorded camera view."""
    lab_background = cv2.cvtColor(environment, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_source = cv2.cvtColor(plate, cv2.COLOR_RGB2LAB).astype(np.float32)
    source_pixels = lab_source[confidence > 0.75]
    outside_pixels = lab_background[known == 0]
    if len(source_pixels) < 100 or len(outside_pixels) < 100:
        return environment

    target_low, target_mid, target_high = np.percentile(source_pixels, (12, 50, 90), axis=0)
    made_low, made_mid, made_high = np.percentile(outside_pixels, (12, 50, 90), axis=0)
    spread = np.clip((target_high - target_low) / np.maximum(made_high - made_low, 3), 0.25, 1.65)
    matched = (lab_background - made_mid) * spread + target_mid
    # A small amount of the generated lighting remains, so a side light still reads as a side light.
    original_amount = 0.08 if target_mid[0] < 76 else 0.20
    matched = matched * (1 - original_amount) + lab_background * original_amount
    return cv2.cvtColor(np.clip(matched, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)



class FluxPanorama:
    """Run the panorama LoRA in a private, local ComfyUI process."""

    CHECKPOINTS = {
        "diffusion_models": "flux-2-klein-base-4b-fp8.safetensors",
        "text_encoders": "qwen_3_4b_fp4_flux2.safetensors",
        "vae": "flux2-vae.safetensors",
        "loras": "flux-2-klein-4B-360-erp-outpaint-lora_V1.safetensors",
    }

    def __init__(self, workspace):
        runtime = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "phoenix-panorama360-runtime"
        self.comfy_root = Path(os.environ.get("VIDEO360_COMFY_ROOT", runtime / "ComfyUI-0.9.2"))
        self.dependencies = runtime / "comfy-packages"
        self.workspace = workspace
        self.input_dir = workspace / "comfy-input"
        self.output_dir = workspace / "comfy-output"
        self.log_path = workspace / "comfy.log"
        self.process = None

    def _request(self, endpoint, data=None):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.address + endpoint, data=payload, headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)

    def _start(self):
        missing = [part for part, filename in self.CHECKPOINTS.items()
                   if not (self.comfy_root / "models" / part / filename).is_file()]
        if not (self.comfy_root / "main.py").is_file() or missing:
            raise RuntimeError(f"本地 FLUX 运行环境未准备好。目录：{self.comfy_root}。缺少权重：{', '.join(missing)}")

        self.input_dir.mkdir()
        self.output_dir.mkdir()
        user_dir = self.workspace / "comfy-user"
        user_dir.mkdir()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            port = port_probe.getsockname()[1]
        self.address = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))
        env = os.environ.copy()
        # Start the actual interpreter, avoiding the virtualenv redirector process on Windows.
        python = sys._base_executable if os.name == "nt" else sys.executable
        paths = [str(self.dependencies), *(part for part in sys.path if part)]
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
        arguments = [
            python, "-u", str(self.comfy_root / "main.py"), "--listen", "127.0.0.1",
            "--port", str(port), "--input-directory", str(self.input_dir),
            "--output-directory", str(self.output_dir), "--temp-directory", str(self.workspace / "comfy-temp"),
            "--user-directory", str(user_dir),
            "--database-url", f"sqlite:///{(user_dir / 'comfy.db').as_posix()}",
            "--disable-all-custom-nodes", "--disable-api-nodes",
            "--disable-metadata", "--preview-method", "none", "--cache-none",
        ]
        with self.log_path.open("wb") as log:
            self.process = subprocess.Popen(
                arguments, cwd=self.comfy_root, env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + 75
        while self.process.poll() is None and time.monotonic() < deadline:
            try:
                self._request("/system_stats")
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"无法启动本地 FLUX 推理。\n{self._log_tail()}")

    def _log_tail(self):
        return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-18:])

    def close(self):
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def generate(self, reference, prompt, seed):
        if self.process is None:
            self._start()
        height, width = reference.shape[:2]
        input_name = f"reference-{uuid.uuid4().hex}.png"
        Image.fromarray(reference).save(self.input_dir / input_name)

        def node(node_type, **inputs):
            return {"class_type": node_type, "inputs": inputs}

        graph = {
            "1": node("LoadImage", image=input_name),
            "2": node("UNETLoader", unet_name=self.CHECKPOINTS["diffusion_models"], weight_dtype="default"),
            "3": node("CLIPLoader", clip_name=self.CHECKPOINTS["text_encoders"], type="flux2", device="default"),
            "4": node("VAELoader", vae_name=self.CHECKPOINTS["vae"]),
            "5": node("LoraLoaderModelOnly", model=["2", 0], lora_name=self.CHECKPOINTS["loras"], strength_model=0.9),
            "6": node("CLIPTextEncode", clip=["3", 0], text=prompt),
            "7": node("CLIPTextEncode", clip=["3", 0], text="people, animals, bird, text, frame, green holes"),
            "8": node("VAEEncode", pixels=["1", 0], vae=["4", 0]),
            "9": node("ReferenceLatent", conditioning=["6", 0], latent=["8", 0]),
            "10": node("ReferenceLatent", conditioning=["7", 0], latent=["8", 0]),
            "11": node("EmptyFlux2LatentImage", width=width, height=height, batch_size=1),
            "12": node("KSampler", model=["5", 0], seed=seed % (2**63), steps=20, cfg=5.0,
                       sampler_name="euler", scheduler="simple", positive=["9", 0], negative=["10", 0],
                       latent_image=["11", 0], denoise=1.0),
            "13": node("VAEDecode", samples=["12", 0], vae=["4", 0]),
            "14": node("SaveImage", images=["13", 0], filename_prefix="panorama"),
        }
        try:
            prompt_id = self._request("/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})["prompt_id"]
        except HTTPError as error:
            raise RuntimeError(f"FLUX 工作流无效：{error.read().decode('utf-8', errors='replace')}") from error

        deadline = time.monotonic() + 900
        while self.process.poll() is None and time.monotonic() < deadline:
            history = self._request(f"/history/{prompt_id}").get(prompt_id)
            if history:
                files = history.get("outputs", {}).get("14", {}).get("images", [])
                if files:
                    spec = files[0]
                    with Image.open(self.output_dir / spec["subfolder"] / spec["filename"]) as output:
                        return np.array(output.convert("RGB"))
                raise RuntimeError(f"本地 FLUX 扩图失败。\n{self._log_tail()}")
            time.sleep(1)
        raise RuntimeError(f"本地 FLUX 扩图未完成。\n{self._log_tail()}")


def refine_matte(estimate):
    # Restrict the soft edges to the dominant connected subject; reject isolated forest details.
    seed = (estimate > 0.52).astype(np.uint8)
    seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(seed)
    if count < 2:
        return np.zeros_like(estimate)
    largest = 1 + int(stats[1:, cv2.CC_STAT_AREA].argmax())
    if stats[largest, cv2.CC_STAT_AREA] < max(24, estimate.size * 0.0005):
        return np.zeros_like(estimate)
    support = cv2.dilate((labels == largest).astype(np.uint8), np.ones((13, 13), np.uint8))
    matte = smoothstep((estimate - 0.27) / 0.46) * support
    return cv2.GaussianBlur(matte, (0, 0), 0.9)


def prepare_sam_mattes(args, cache_dir, frame_count):
    """Compute the boxed foreground mattes before launching panorama inference."""
    with closing(imageio_ffmpeg.read_frames(
        str(args.video), output_params=["-map", "0:v:0", "-frames:v", "1"]
    )) as original:
        source_width, source_height = next(original)["size"]
    left, top, right, bottom = args.sam_box
    if right > source_width or bottom > source_height:
        raise RuntimeError(f"--sam-box 超出原视频尺寸 {source_width}×{source_height}")

    import torch
    try:
        from segment_anything import SamPredictor, sam_model_registry
    except ImportError as error:
        raise RuntimeError("SAM 模式需要在当前 Python 环境安装 torchvision 和官方 segment-anything") from error

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"使用 SAM ViT-B 框选提取 {frame_count} 帧前景（{device}）……", flush=True)
    model = sam_model_registry["vit_b"]()
    model.load_state_dict(torch.load(args.sam_checkpoint, map_location="cpu", weights_only=True))
    predictor = SamPredictor(model.to(device).eval())
    cache_dir.mkdir()
    written = 0
    with closing(read_frames(args, 1920)) as frames:
        for index, frame in enumerate(frames):
            height, width = frame.shape[:2]
            x1 = math.floor(left * width / source_width)
            y1 = math.floor(top * height / source_height)
            x2 = math.ceil(right * width / source_width)
            y2 = math.ceil(bottom * height / source_height)
            padding = max(24, int(0.12 * max(x2 - x1, y2 - y1)))
            x0, y0 = max(0, x1 - padding), max(0, y1 - padding)
            xr, yr = min(width, x2 + padding), min(height, y2 + padding)
            box = np.array([x1 - x0, y1 - y0, x2 - x0 - 1, y2 - y0 - 1])
            with torch.inference_mode(), torch.autocast(
                "cuda", dtype=torch.float16, enabled=device == "cuda"
            ):
                predictor.set_image(frame[y0:yr, x0:xr])
                logits, scores, _ = predictor.predict(
                    box=box, multimask_output=True, return_logits=True
                )
            estimate = np.zeros((height, width), np.float32)
            estimate[y0:yr, x0:xr] = smoothstep((logits[int(np.argmax(scores))] + 2.8) / 5.6)
            matte = np.rint(refine_matte(estimate) * 255).astype(np.uint8)
            Image.fromarray(matte).save(cache_dir / f"{index:06d}.png")
            written += 1
    del predictor, model
    if device == "cuda":
        torch.cuda.empty_cache()
    if written != frame_count:
        raise RuntimeError(f"SAM 前景蒙版帧数不符（{written}/{frame_count}）")


def read_sam_matte(cache_dir, index, size):
    with Image.open(cache_dir / f"{index:06d}.png") as image:
        matte = np.asarray(image, dtype=np.float32) / 255
    if matte.shape[1::-1] != size:
        matte = cv2.resize(matte, size, interpolation=cv2.INTER_LINEAR)
    return matte


def feather_seam(image):
    output = image.copy()
    band = max(8, min(48, image.shape[1] // 24))
    left = image[:, :band].astype(np.float32)
    right = image[:, -band:][:, ::-1].astype(np.float32)
    shared = (left + right) * 0.5
    preserve = smoothstep(np.linspace(0, 1, band, dtype=np.float32))[None, :, None]
    output[:, :band] = shared * (1 - preserve) + left * preserve
    output[:, -band:] = (shared * (1 - preserve) + right * preserve)[:, ::-1]
    return output



class PanoramaGenerator:
    def __init__(self, offline, outpainter):
        self.outpainter = outpainter
        if offline:
            os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        import torch
        from transformers import BlipForConditionalGeneration, BlipProcessor

        self.torch = torch
        print("加载本地 BLIP 自动描述模型……", flush=True)
        def load_model(model_class, name, **options):
            try:
                return model_class.from_pretrained(name, local_files_only=True, **options)
            except OSError:
                if offline:
                    raise
                return model_class.from_pretrained(name, **options)

        self.caption_processor = load_model(BlipProcessor, CAPTION_MODEL, use_fast=False)
        self.caption_model = load_model(BlipForConditionalGeneration, CAPTION_MODEL).eval()
    def caption(self, image):
        data = self.caption_processor(Image.fromarray(image), return_tensors="pt")
        with self.torch.inference_mode():
            result = self.caption_model.generate(**data, max_new_tokens=22, num_beams=2)
        return self.caption_processor.decode(result[0], skip_special_tokens=True)

    def generate(self, plate, confidence, fov, seed):
        label = self.caption(plate)
        print(f"自动描述：{label}", flush=True)
        words = label.lower().split()
        wooded = any(word in words for word in ("forest", "woods", "trees", "woodland"))
        luma = cv2.cvtColor(plate, cv2.COLOR_RGB2GRAY)
        dark = float(np.percentile(luma, 75)) < 88
        color = np.median(plate.reshape(-1, 3), axis=0)
        cool = color[2] - color[0] > 4
        lighting = "dim low-key illumination with deep shadows" if dark else "natural source lighting"
        if dark and cool:
            lighting += ", blue ambient light"
        if wooded:
            label = "dense forest, moss, roots, trees and wooded ground"
            if dark:
                lighting += ", nocturnal forest with clear bark, roots and detailed moss"
        width, height = 2048, 1024
        projection = FrontProjection(width, height, plate.shape[1::-1], fov)
        region = projection.region
        reference = np.zeros((height, width, 3), np.uint8)
        reference[:] = [0, 255, 0]
        # The green mask includes the off-camera surroundings and any floor never visible behind the actor.
        # Filling them as one image keeps the reconstructed ground consistent with the wider environment.
        available = projection.coverage & (projection.sample(confidence) > 0.98)
        reference[region][available] = projection.sample(plate)[available]
        known = np.zeros((height, width), np.uint8)
        known[region][available] = 255
        prompt = (
            "Fill the green spaces according to the image. Outpaint as a seamless 360 equirectangular "
            "panorama (2:1). Keep the horizon level. Match left and right edges. "
            f"Continue the original {label} with {lighting}. Preserve its camera position and color palette. "
            "Show a single continuous empty environment, with uninterrupted ground and sky and clearly resolved textures."
        )
        print("用本地 FLUX 360° LoRA 补全一张新底图……", flush=True)
        image = self.outpainter.generate(reference, prompt, seed)
        if image.shape != reference.shape:
            raise RuntimeError(f"全景底图尺寸与预期不符：{image.shape}")
        green = (image[..., 1] > 175) & (image[..., 0] < 35) & (image[..., 2] < 35)
        if green.mean() > 0.012:
            raise RuntimeError("扩图后仍有大块未补齐的绿色区域")
        image = harmonize_palette(image, plate, confidence, known)
        ambience = ForestAmbience(seed) if wooded and dark else None
        return feather_seam(image), ambience



class ForestAmbience:
    """A small, longitude-wrapped mist and particle layer for dark wooded scenes."""

    def __init__(self, seed):
        self.width, self.height = 384, 192
        rng = np.random.default_rng(seed)
        self.particles = rng.uniform(0, 1, (20, 4))
        self.lon = np.arange(self.width, dtype=np.float32)[None, :] / self.width * math.tau
        vertical = np.arange(self.height, dtype=np.float32)[:, None] / self.height
        self.band = np.exp(-((vertical - 0.62) / 0.24) ** 2)
        self.vertical = vertical

    def light(self, seconds, output_size):
        wave = np.sin(3 * self.lon + self.vertical * 8 - seconds * 0.9)
        wisps = np.sin(7 * self.lon - self.vertical * 17 + seconds * 0.55)
        density = self.band * np.maximum((wave + 0.45 * wisps) * 4, 0)
        layer = np.clip(density[..., None] * [0.55, 0.83, 1.0], 0, 255).astype(np.uint8)
        points = np.zeros_like(layer)
        for offset, altitude, phase, speed in self.particles:
            x = round(((offset + seconds * (speed - 0.5) * 0.03) % 1) * self.width)
            y = round((0.43 + altitude * 0.32) * self.height + 2 * math.sin(seconds + phase * math.tau))
            glow = max(0, math.sin(seconds * 1.8 + phase * math.tau))
            color = (18 * glow, 49 * glow, 23 * glow)
            for wrap in (-self.width, 0, self.width):
                cv2.circle(points, (x + wrap, y), 1, color, -1, cv2.LINE_AA)
        halo = cv2.GaussianBlur(points, (0, 0), 1.5)
        layer = cv2.add(layer, cv2.add(points, halo))
        return cv2.resize(layer, output_size, interpolation=cv2.INTER_LINEAR)


def render(args, starts, samples, frame_count, foreground, generator, target, *, sam_mattes=None):
    size = (args.width, args.width // 2)

    def matte_at(index, frame):
        if sam_mattes is not None:
            return read_sam_matte(sam_mattes, index, frame.shape[1::-1])
        return refine_matte(foreground.mask(frame))

    command = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(args.fps), "-i", "-",
    ]
    if args.start:
        command += ["-ss", str(args.start)]
    command += [
        "-i", str(args.video), "-map", "0:v:0", "-map", "1:a:0?",
        "-t", f"{frame_count / args.fps:.9f}", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", "-threads:v", "6",
        "-vf", "scale=in_range=full:out_range=limited:out_color_matrix=bt709", "-color_range", "tv",
        "-color_primaries", "bt709", "-colorspace", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target),
    ]
    reference_limit = max(1920, min(args.width, 3840))
    with (
        closing(read_frames(args, reference_limit)) as reference_frames,
        closing(read_frames(args, 1920)) as video_frames,
        subprocess.Popen(command, stdin=subprocess.PIPE) as job,
    ):
        reference_stream = enumerate(reference_frames)
        shot = 0
        written = 0
        try:
            for index, original in enumerate(video_frames):
                if shot < len(starts) and index == starts[shot]:
                    print(f"生成镜头 {shot + 1}/{len(starts)} 的全景背景……", flush=True)
                    sample_indices = samples[shot]
                    frames = collect_shot_samples(reference_stream, sample_indices)
                    if sam_mattes is None:
                        plate, known = clean_background(frames, foreground)
                    else:
                        plate, known = clean_background(
                            frames, foreground,
                            matte_for_sample=lambda slot, frame: matte_at(sample_indices[slot], frame),
                        )
                    del frames
                    sw, sh = original.shape[1::-1]
                    fov = math.degrees(2 * math.atan(math.tan(math.radians(59)) * min(1.0, sw / sh)))
                    panorama, ambience = generator.generate(plate, known, fov, args.seed + shot * 31)
                    shot_start = index
                    background = preserve_recorded_background(panorama, plate, known, fov, size)
                    projection = FrontProjection(*size, (sw, sh), fov)
                    shot += 1

                if ambience is None:
                    frame = background.copy()
                else:
                    elapsed = (index - shot_start) / args.fps
                    frame = cv2.add(background, ambience.light(elapsed, size))
                region = projection.region
                matte = matte_at(index, original)
                # Project premultiplied foreground to prevent the old 16:9 floor from bleeding into the edges.
                alpha = projection.sample(matte) * projection.coverage
                projected = projection.sample(original.astype(np.float32) * matte[..., None])
                frame[region] = np.clip(
                    frame[region] * (1 - alpha[..., None]) + projected * projection.coverage[..., None], 0, 255,
                ).astype(np.uint8)
                job.stdin.write(memoryview(frame))
                written += 1
                if written % (args.fps * 5) == 0:
                    print(f"已合成 {written}/{frame_count} 帧。", flush=True)
        finally:
            job.stdin.close()
        if job.wait() != 0:
            raise RuntimeError("FFmpeg 编码失败")
        if written != frame_count:
            raise RuntimeError(f"两次解码的帧数不一致（{written}/{frame_count}）")


def main():
    args = parse_args()
    cv2.setNumThreads(2)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    starts, samples, count = find_shots(args)
    foreground = ForegroundModel(args.offline) if args.sam_box is None else None
    with tempfile.TemporaryDirectory(prefix="video360-", dir=args.output.parent) as temp:
        workspace = Path(temp)
        pending = workspace / "panorama.mp4"
        sam_mattes = None
        if args.sam_box is not None:
            sam_mattes = workspace / "sam-mattes"
            prepare_sam_mattes(args, sam_mattes, count)
        outpainter = FluxPanorama(workspace)
        try:
            generator = PanoramaGenerator(args.offline, outpainter)
            render(args, starts, samples, count, foreground, generator, pending, sam_mattes=sam_mattes)
        finally:
            outpainter.close()
        if args.output.exists():
            raise RuntimeError(f"输出已存在：{args.output}")
        pending.rename(args.output)
    print(f"完成：{args.output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as error:
        print(f"转换失败：{error}", file=sys.stderr)
        sys.exit(1)

