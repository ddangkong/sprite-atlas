"""FLUX Kontext masked-region edit — backend for the atlas '부분 수정' feature.

Reuses ComfyUI's /prompt API and the existing `flux_kontext_consistency`
workflow, then injects three nodes so ONLY the user-painted region is replaced:

    LoadImage(mask) -> ImageToMask -> ImageCompositeMasked(dest=original,
                                                           source=kontext_edit,
                                                           mask=user_mask)

Kontext regenerates the whole frame guided by the original (ReferenceLatent) +
the edit prompt; compositing the masked region back over the *exact* original
keeps everything outside the mask pixel-identical. The unmasked area never
drifts, so a localized edit stays localized.

This module is import-safe (no heavy deps) and is called from server.py.
ComfyUI itself must be running on :8188 (see run_comfyui.bat).
"""
from __future__ import annotations

import json
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

COMFY_URL = "http://127.0.0.1:8188"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKFLOW_PATH = HERE / "workflows" / "flux_kontext_consistency.json"
COMFY_OUTPUT = HERE / "output"
COMFY_INPUT = HERE / "input"

# Widget input-name maps so the GUI workflow can be addressed by name in the
# /prompt API. Mirrors quick_demo.py and adds the two mask nodes we inject.
_WIDGET_NAMES = {
    "UNETLoader": ["unet_name", "weight_dtype"],
    "DualCLIPLoader": ["clip_name1", "clip_name2", "type", "device"],
    "VAELoader": ["vae_name"],
    "LoadImage": ["image", "upload"],
    "CLIPTextEncode": ["text"],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg",
                 "sampler_name", "scheduler", "denoise"],
    "SaveImage": ["filename_prefix"],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "ImageToMask": ["channel"],
    "ImageCompositeMasked": ["x", "y", "resize_source"],
}


def _widget_names_for(node_type: str) -> list[str]:
    return _WIDGET_NAMES.get(node_type, [])


def comfy_up(timeout: float = 3.0) -> bool:
    """True if ComfyUI is reachable on :8188."""
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def load_workflow() -> dict:
    """GUI-format workflow (nodes/links) -> prompt-API format (id -> {class_type, inputs}).

    Links live both in the top-level `links` array and each node's
    `inputs[i].link`; we index links by id then resolve each input slot's
    source so fan-out (one link feeding several inputs) is preserved.
    """
    raw = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    nodes = {str(n["id"]): n for n in raw["nodes"]}
    link_src: dict[int, tuple[str, int]] = {}
    for link in raw.get("links", []):
        link_id, src_node, src_slot = link[0], link[1], link[2]
        link_src[link_id] = (str(src_node), src_slot)

    prompt: dict[str, dict] = {}
    for nid, node in nodes.items():
        inputs: dict = {}
        for slot in node.get("inputs", []) or []:
            link_id = slot.get("link")
            if link_id is None:
                continue
            src = link_src.get(link_id)
            if src is not None:
                inputs[slot["name"]] = [src[0], src[1]]
        for name, val in zip(_widget_names_for(node["type"]), node.get("widgets_values") or []):
            inputs[name] = val
        prompt[nid] = {"class_type": node["type"], "inputs": inputs}
    return prompt


def upload_image(local_path: Path, dest_name: str | None = None) -> str:
    """Upload a PNG into ComfyUI's input/ via /upload/image. Returns the
    filename ComfyUI will reference. `dest_name` overrides the stored name
    (use a unique name to avoid input/ collisions across requests)."""
    name = dest_name or local_path.name
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{name}"'.encode(),
        b"Content-Type: image/png", b"",
        local_path.read_bytes(),
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="overwrite"', b"", b"true",
        f"--{boundary}--".encode(),
    ]
    payload = b"\r\n".join(parts)
    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image", data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]


def queue_prompt(prompt: dict) -> str:
    payload = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(
        f"{COMFY_URL}/prompt", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ComfyUI /prompt {e.code}: {body}") from None


def wait_for(prompt_id: str, poll_sec: float = 1.5, timeout_sec: float = 300.0) -> list[str]:
    """Poll /history/{id} until done. Returns output PNG filenames in output/."""
    start = time.time()
    while True:
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"prompt {prompt_id} did not finish in {timeout_sec}s")
        with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=10) as r:
            hist = json.loads(r.read())
        if prompt_id in hist:
            files: list[str] = []
            for node_out in hist[prompt_id].get("outputs", {}).values():
                for img in node_out.get("images", []):
                    files.append(img["filename"])
            return files
        time.sleep(poll_sec)


def _find_by_class(prompt: dict, class_type: str) -> str:
    for nid, n in prompt.items():
        if n["class_type"] == class_type:
            return nid
    raise KeyError(class_type)


def _find_pos_prompt_id(raw_nodes: list[dict]) -> str:
    """Positive prompt node = the CLIPTextEncode titled '변경 프롬프트…'.
    Falls back to the first CLIPTextEncode that is NOT titled NEGATIVE."""
    for n in raw_nodes:
        if "변경 프롬프트" in (n.get("title") or ""):
            return str(n["id"])
    for n in raw_nodes:
        if n.get("type") == "CLIPTextEncode" and "negative" not in (n.get("title") or "").lower():
            return str(n["id"])
    raise KeyError("positive CLIPTextEncode")


def _next_id(prompt: dict) -> str:
    return str(max(int(k) for k in prompt) + 1)


def build_mask_edit_workflow(image_filename: str, mask_filename: str,
                             prompt_text: str, seed: int,
                             out_prefix: str = "flux_edit") -> dict:
    """Base Kontext workflow + mask-composite so only the painted region changes."""
    prompt = load_workflow()
    raw = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    pos_id = _find_pos_prompt_id(raw["nodes"])
    load_id = _find_by_class(prompt, "LoadImage")
    ksampler_id = _find_by_class(prompt, "KSampler")
    vaedecode_id = _find_by_class(prompt, "VAEDecode")
    save_id = _find_by_class(prompt, "SaveImage")

    prompt[pos_id]["inputs"]["text"] = prompt_text
    prompt[load_id]["inputs"]["image"] = image_filename
    prompt[ksampler_id]["inputs"]["seed"] = int(seed)

    mask_load = _next_id(prompt)
    prompt[mask_load] = {"class_type": "LoadImage",
                         "inputs": {"image": mask_filename, "upload": "image"}}
    to_mask = _next_id(prompt)
    prompt[to_mask] = {"class_type": "ImageToMask",
                       "inputs": {"image": [mask_load, 0], "channel": "red"}}
    composite = _next_id(prompt)
    prompt[composite] = {"class_type": "ImageCompositeMasked",
                         "inputs": {"destination": [load_id, 0],
                                    "source": [vaedecode_id, 0],
                                    "mask": [to_mask, 0],
                                    "x": 0, "y": 0, "resize_source": True}}
    # SaveImage now saves the composited result, not the raw Kontext output.
    prompt[save_id]["inputs"]["images"] = [composite, 0]
    prompt[save_id]["inputs"]["filename_prefix"] = out_prefix
    return prompt


def edit_frame(src_png: Path, mask_png: Path, prompt_text: str,
               seed: int = 42, out_prefix: str = "flux_edit") -> Path:
    """Run one masked Kontext edit. Uploads the original + mask, queues the
    workflow, waits, and returns the path of the composited output PNG."""
    if not comfy_up():
        raise RuntimeError("ComfyUI is not running on :8188 (start run_comfyui.bat)")
    tag = uuid.uuid4().hex[:8]
    img_fn = upload_image(src_png, dest_name=f"fluxsrc_{tag}.png")
    mask_fn = upload_image(mask_png, dest_name=f"fluxmask_{tag}.png")
    wf = build_mask_edit_workflow(img_fn, mask_fn, prompt_text, seed, out_prefix=f"{out_prefix}_{tag}")
    pid = queue_prompt(wf)
    files = wait_for(pid)
    if not files:
        raise RuntimeError("ComfyUI returned no output image")
    return COMFY_OUTPUT / files[-1]
