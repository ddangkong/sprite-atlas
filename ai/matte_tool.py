"""Re-matte sprite frames (background removal) — atlas '누끼 다시 따기'.

Uses transparent_background (InSPyReNet) on CPU — the GPU is busy with FLUX,
and InSPyReNet gives clean character cutouts (alpha) even on the beige raw
frames where the old flood-fill matte failed. Runs as a subprocess invoked by
server.py with the comfyui venv python (which has the lib).

CLI:
    matte_tool.py --src in.png --out out.png            # one frame
    matte_tool.py --indir <anims/action> --status j.json # all f*.png in place
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

_remover = None


def get_remover():
    global _remover
    if _remover is None:
        from transparent_background import Remover
        _remover = Remover(device="cpu")  # model is cached after first download
    return _remover


def matte_one(src: Path, out: Path) -> None:
    img = Image.open(src).convert("RGB")
    res = get_remover().process(img, type="rgba")
    if getattr(res, "mode", None) != "RGBA":
        res = res.convert("RGBA")
    out.parent.mkdir(parents=True, exist_ok=True)
    res.save(out, format="PNG")


def _status(path: str | None, **kw) -> None:
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(kw), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--indir")
    ap.add_argument("--status")
    a = ap.parse_args()

    try:
        get_remover()  # may download the InSPyReNet weights on first run
    except Exception as e:
        _status(a.status, state="error", done=0, total=0, message=f"init: {e}")
        print("ERR init:", e, file=sys.stderr)
        return 1

    if a.src and a.out:
        matte_one(Path(a.src), Path(a.out))
        print("OK", a.out)
        return 0

    if a.indir:
        d = Path(a.indir)
        frames = sorted(d.glob("f*.png"), key=lambda p: int(p.stem[1:]))
        _status(a.status, state="running", done=0, total=len(frames))
        errs = 0
        for i, fp in enumerate(frames, 1):
            try:
                matte_one(fp, fp)  # in place
            except Exception as e:
                errs += 1
                print("frame err", fp, e, file=sys.stderr)
            _status(a.status, state="running", done=i, total=len(frames))
        _status(a.status, state="done", done=len(frames), total=len(frames), message=f"errors={errs}")
        print("OK batch", len(frames), "errs", errs)
        return 0

    print("nothing to do (need --src/--out or --indir)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
