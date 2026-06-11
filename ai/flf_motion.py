"""FLF2V motion — backend for the atlas '🎞 모션 생성' feature.

Two images (start + end) -> a Wan 2.2 First-Last-Frame-to-Video interpolation ->
N PNG frames, fetched over ComfyUI's HTTP /view (so it does NOT depend on where
ComfyUI writes its output). Frames are background-matted (border flood-fill, so a
white-haired character is preserved) and returned ready to drop into an action
folder as f1..fN.png.

Self-contained: only stdlib urllib + Pillow. ComfyUI must run on :8188 with the
Wan 2.2 i2v models + lightx2v LoRA (same models the project already uses).
Called from server.py.
"""
from __future__ import annotations
import io, json, time, uuid, urllib.request, urllib.parse, urllib.error
from PIL import Image, ImageDraw, ImageChops, ImageFilter

COMFY_URL = "http://127.0.0.1:8188"

# Model filenames (match the project's existing Wan 2.2 i2v setup).
UNET_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
UNET_LOW  = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
LORA_HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
LORA_LOW  = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
CLIP_NAME = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE_NAME  = "wan_2.1_vae.safetensors"
DEF_NEG = ("blurry, low quality, low resolution, deformed, distorted, extra limbs, "
           "extra fingers, watermark, text, jpeg artifacts, border, frame")


def comfy_up(timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _upload(img_bytes: bytes, name: str) -> str:
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{name}"'.encode(),
        b"Content-Type: image/png", b"", img_bytes,
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="overwrite"', b"", b"true",
        f"--{boundary}--".encode(),
    ]
    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image", data=b"\r\n".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]


def _queue(prompt: dict) -> str:
    payload = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI /prompt {e.code}: {e.read().decode('utf-8','replace')}") from None


def _wait(pid: str, timeout_sec: float = 900.0, poll: float = 1.5) -> list[dict]:
    """Poll /history/{pid}; return the SaveImage output image dicts."""
    start = time.time()
    while True:
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"prompt {pid} timed out after {timeout_sec}s")
        with urllib.request.urlopen(f"{COMFY_URL}/history/{pid}", timeout=10) as r:
            hist = json.loads(r.read())
        if pid in hist:
            st = hist[pid].get("status", {}).get("status_str")
            if st == "error":
                raise RuntimeError("ComfyUI reported an error running the workflow")
            imgs: list[dict] = []
            for node_out in hist[pid].get("outputs", {}).values():
                for im in node_out.get("images", []):
                    imgs.append(im)
            if imgs:
                return imgs
        time.sleep(poll)


def _view(img: dict) -> Image.Image:
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    with urllib.request.urlopen(f"{COMFY_URL}/view?{q}", timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def _build(start_fn: str, end_fn: str, prompt: str, neg: str,
           length: int, w: int, h: int, seed: int, steps: int, shift: float,
           prefix: str) -> dict:
    half = max(1, steps // 2)
    return {
 "10": {"class_type": "LoadImage", "inputs": {"image": start_fn, "upload": "image"}},
 "11": {"class_type": "LoadImage", "inputs": {"image": end_fn, "upload": "image"}},
 "95": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_HIGH, "weight_dtype": "default"}},
 "96": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_LOW, "weight_dtype": "default"}},
 "101": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["95", 0], "lora_name": LORA_HIGH, "strength_model": 1.0}},
 "102": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["96", 0], "lora_name": LORA_LOW, "strength_model": 1.0}},
 "103": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["101", 0], "shift": float(shift)}},
 "104": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["102", 0], "shift": float(shift)}},
 "84": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "wan", "device": "default"}},
 "90": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_NAME}},
 "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["84", 0]}},
 "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg or DEF_NEG, "clip": ["84", 0]}},
 "98": {"class_type": "WanFirstLastFrameToVideo", "inputs": {"positive": ["6", 0], "negative": ["7", 0], "vae": ["90", 0], "width": int(w), "height": int(h), "length": int(length), "batch_size": 1, "start_image": ["10", 0], "end_image": ["11", 0]}},
 "86": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["103", 0], "add_noise": "enable", "noise_seed": int(seed), "steps": int(steps), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["98", 0], "negative": ["98", 1], "latent_image": ["98", 2], "start_at_step": 0, "end_at_step": half, "return_with_leftover_noise": "enable"}},
 "85": {"class_type": "KSamplerAdvanced", "inputs": {"model": ["104", 0], "add_noise": "disable", "noise_seed": int(seed), "steps": int(steps), "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["98", 0], "negative": ["98", 1], "latent_image": ["86", 0], "start_at_step": half, "end_at_step": int(steps), "return_with_leftover_noise": "disable"}},
 "8": {"class_type": "VAEDecode", "inputs": {"samples": ["85", 0], "vae": ["90", 0]}},
 "58": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": prefix}},
}


def matte(im: Image.Image, size: int = 512, thresh: int = 90) -> Image.Image:
    """Border flood-fill background removal -> RGBA, resized to size×size.
    Interior near-white pixels (e.g. white hair) are NOT removed."""
    im = im.convert("RGB"); W, H = im.size
    work = im.copy()
    for pt in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]:
        ImageDraw.floodfill(work, pt, (255, 0, 255), thresh=thresh)
    r, g, b = work.split()
    rb = r.point(lambda v: 255 if v == 255 else 0)
    bb = b.point(lambda v: 255 if v == 255 else 0)
    gb = g.point(lambda v: 255 if v == 0 else 0)
    bg = ImageChops.multiply(ImageChops.multiply(rb, bb), gb)   # 255 where magenta(bg)
    out = im.convert("RGBA"); out.putalpha(ImageChops.invert(bg))
    out = out.resize((size, size), Image.LANCZOS)
    a = out.split()[3].filter(ImageFilter.GaussianBlur(0.5)); out.putalpha(a)
    return out


def generate(start_png: bytes, end_png: bytes, prompt: str, neg: str = "",
             length: int = 25, width: int = 896, height: int = 896,
             seed: int = 0, steps: int = 4, shift: float = 8.0) -> list[Image.Image]:
    """Run one FLF2V interpolation. Returns the raw RGB frames (not yet matted)."""
    if not comfy_up():
        raise RuntimeError("ComfyUI is not running on :8188")
    tag = uuid.uuid4().hex[:8]
    s_fn = _upload(start_png, f"flf_s_{tag}.png")
    e_fn = _upload(end_png, f"flf_e_{tag}.png")
    wf = _build(s_fn, e_fn, prompt, neg, length, width, height, seed, steps, shift, f"flf_{tag}")
    pid = _queue(wf)
    imgs = _wait(pid)
    return [_view(im) for im in imgs]
