"""Auto-matte — prompt-free background removal for a whole project.

Border flood-fill keeps a white-haired character intact (interior light pixels
are never border-connected), while faded afterimage/trail pixels survive as
translucent ghosts (alpha follows darkness in the flooded region). Designed for
the white-background sprites this tool's ComfyUI pipeline produces.

No ComfyUI, no model download, no prompt — only numpy + Pillow. Frames that
already look matted (enough transparency) are skipped, so it is idempotent and
cheap to re-run after dropping in new files.
"""
from __future__ import annotations
import glob, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def hybrid_matte(im: Image.Image, size: int | None = None, thresh: int = 90) -> Image.Image:
    im = im.convert("RGB"); W, H = im.size
    work = im.copy()
    for pt in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]:
        ImageDraw.floodfill(work, pt, (255, 0, 255), thresh=thresh)
    a = np.asarray(work)
    flooded = (a[:, :, 0] == 255) & (a[:, :, 1] == 0) & (a[:, :, 2] == 255)
    arr = np.asarray(im).astype(np.float32)
    dark = 255.0 - arr.min(axis=2)                       # 0 for white, larger for dark
    a_out = np.clip((dark - 12) * 3.0, 0, 255)            # flooded: white->transparent, trail->translucent
    alpha = np.where(flooded, a_out, 255).astype("uint8")  # inside (incl. hair): opaque
    out = Image.fromarray(np.dstack([np.asarray(im), alpha]), "RGBA")
    if size:
        out = out.resize((size, size), Image.LANCZOS)
    al = out.split()[3].filter(ImageFilter.GaussianBlur(0.4)); out.putalpha(al)
    return out


def needs_matte(path: str) -> bool:
    """True if the PNG still has an opaque background (not yet matted)."""
    try:
        im = Image.open(path)
        if im.mode != "RGBA":
            return True
        a = np.asarray(im.convert("RGBA"))[:, :, 3]
        return float((a < 16).mean()) < 0.15
    except Exception:
        return False


def iter_frames(base_dir: str):
    """All f*.png under <base>/<category>/<char>/anims/<action>/."""
    return sorted(glob.glob(os.path.join(base_dir, "*", "*", "anims", "*", "f*.png")))


def matte_base(base_dir: str, on_progress=None, thresh: int = 90):
    """Matte every un-matted frame in a project base, in place. Returns
    (matted, skipped, total)."""
    frames = iter_frames(base_dir)
    total = len(frames); matted = skipped = 0
    for i, fp in enumerate(frames, 1):
        try:
            if needs_matte(fp):
                hybrid_matte(Image.open(fp), thresh=thresh).save(fp)
                matted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
        if on_progress:
            on_progress(i, total, matted, skipped)
    return matted, skipped, total
