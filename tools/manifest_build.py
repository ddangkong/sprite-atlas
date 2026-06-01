"""
Copy generated PNGs from comfyui/output/ to static/sprites/ in a clean
per-character folder layout, then emit manifest.json.

OUTPUT LAYOUT (per-character):
  pets/
    egg/
      neutral.png, happy.png, sad.png, sick.png, sleeping.png, dead.png   (statics)
      anims/
        idle/   f1.png, f2.png, f3.png, f4.png
        sleep/  ...
        wobble/ ...
        ...
    baby/
      ...
  monsters/
    happymong/
      idle.png, active.png            (legacy statics)
      anims/
        idle/, attack/, skill/, hurt/, sleeping/, angry/, surprise/
    ...
  bosses/
    gulkingmong/
      idle.png, attack.png, hurt.png, win.png   (legacy statics)
      anims/
        idle/, attack/, hurt/, win/, charge/, enraged/, skill/, intro/
    ...
  baits/  (flat, single image per item)
  locations/  (flat)

ComfyUI source names look like `pet_baby_eat_f3_00001_.png`. We parse them
into (category, base, suffix-or-scenario-frame) and route accordingly.

The function also migrates older flat names (e.g. `pets/baby_neutral.png`)
into the new structure so the layout becomes consistent.

Run after generate_frames.py finishes:
    py comfyui/copy_to_game.py
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    from pixelize import mosaic_pixelize  # type: ignore
    HAS_PIXELIZE = True
except Exception:
    HAS_PIXELIZE = False

# FLUX Kontext outputs at 1024x1024 — way more than the game needs. Resize
# to TARGET_SIZE during copy. None = keep original.
TARGET_SIZE = 512

# When True, every imported PNG is run through mosaic_pixelize() (128px
# logical grid, 32-color palette, NEAREST upscale) before saving. Toggled
# by the --pixelize CLI flag in main(). Server endpoints flip this in
# memory before calling _import_png.
PIXELIZE_ENABLED = False
PIXELIZE_PX = 128
PIXELIZE_COLORS = 32

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "comfyui" / "output"
DST = ROOT / "static" / "sprites"

# singular (output prefix) → (plural folder, base path)
CAT_INFO = {
    "pet":      ("pets",      DST / "pets"),
    "monster":  ("monsters",  DST / "monsters"),
    "boss":     ("bosses",    DST / "bosses"),
    "bait":     ("baits",     DST / "baits"),
    "location": ("locations", DST / "locations"),
}

# Animation scenarios — used to split base_scenario from a stem like "baby_eat_f3"
KNOWN_SCENARIOS = {
    # Pet
    "idle", "eat", "sleep", "happy", "sad", "talk", "angry", "surprise",
    "attack", "skill", "hurt", "win", "walk", "wobble", "crack",
    "hatch_glow", "evolution_complete",
    # Monster
    "sleeping",
    # Boss
    "charge", "enraged", "intro",
}

# Static-only suffixes (legacy single-frame variants)
PET_MOOD_STATICS   = {"happy", "neutral", "sad", "sick", "sleeping", "dead"}
MONSTER_STATICS    = {"idle", "active"}
BOSS_STATICS       = {"idle", "attack", "hurt", "win"}


def parse_output_name(name: str) -> tuple[str, str] | None:
    """`pet_baby_eat_f3_00001_.png` → ('pet', 'baby_eat_f3').
    Strips the `_NNNNN_` suffix added by ComfyUI's SaveImage."""
    m = re.match(r"^(pet|monster|boss|bait|location)_(.+?)_\d{5}_\.png$", name)
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_stem(stem: str) -> tuple[str, str, int | None]:
    """`baby_eat_f3` → ('baby', 'eat', 3).
    `baby_neutral` → ('baby', 'neutral', None) — static.
    `happymong_idle` → ('happymong', 'idle', None) — static legacy."""
    # Frame-N animation form
    m = re.match(r"^(.+)_f(\d+)$", stem)
    if m:
        prefix, frame_num = m.group(1), int(m.group(2))
        for scen in sorted(KNOWN_SCENARIOS, key=len, reverse=True):
            needle = "_" + scen
            if prefix.endswith(needle):
                base = prefix[: -len(needle)]
                if base:
                    return base, scen, frame_num
    # Static — last `_word` is the variant suffix
    m2 = re.match(r"^(.+)_([^_]+)$", stem)
    if m2:
        return m2.group(1), m2.group(2), None
    return stem, "", None


def route_target(category: str, stem: str) -> Path | None:
    """Compute destination path under static/sprites/ for one parsed stem.

    Examples:
      pet  baby_eat_f3      → pets/baby/anims/eat/f3.png
      pet  baby_neutral     → pets/baby/neutral.png
      pet  egg_idle_f2      → pets/egg/anims/idle/f2.png
      monster happymong_attack_f3 → monsters/happymong/anims/attack/f3.png
      monster happymong_idle      → monsters/happymong/idle.png
      bait honey_pot        → baits/honey_pot.png        (flat — no folder)
    """
    info = CAT_INFO.get(category)
    if not info:
        return None
    plural, plural_dir = info
    # Bait + location stay flat (one image each)
    if category in ("bait", "location"):
        return plural_dir / f"{stem}.png"
    base, suffix, frame_num = parse_stem(stem)
    if not base or not suffix:
        return None
    char_dir = plural_dir / base
    if frame_num is not None:
        return char_dir / "anims" / suffix / f"f{frame_num}.png"
    # Static suffix
    return char_dir / f"{suffix}.png"


# ---------------------------------------------------------------------------

def _import_png(src: Path, dst: Path) -> None:
    """Copy `src` → `dst`, applying (optional) mosaic pixelization and then
    LANCZOS-downscaling to TARGET_SIZE if PIL is available and the source
    is larger. Falls back to plain shutil.copy2 otherwise."""
    # Mosaic pixelize path: replaces the LANCZOS resize with NEAREST-grid
    # quantization. Final output is at TARGET_SIZE (the pixelizer's display
    # upscale handles that).
    if PIXELIZE_ENABLED and HAS_PIXELIZE and HAS_PIL:
        try:
            with Image.open(src) as im:
                out = mosaic_pixelize(
                    im,
                    target_px=PIXELIZE_PX,
                    colors=PIXELIZE_COLORS,
                    display_px=TARGET_SIZE or PIXELIZE_PX,
                )
                out.save(dst, format="PNG", optimize=True)
                return
        except Exception as e:
            print(f"   ! pixelize failed for {src.name}: {e} — falling back to LANCZOS")

    if TARGET_SIZE and HAS_PIL:
        try:
            with Image.open(src) as im:
                if im.size[0] > TARGET_SIZE or im.size[1] > TARGET_SIZE:
                    # Preserve aspect ratio by fitting within TARGET_SIZE x TARGET_SIZE
                    w, h = im.size
                    if w >= h:
                        new_w = TARGET_SIZE
                        new_h = round(h * TARGET_SIZE / w)
                    else:
                        new_h = TARGET_SIZE
                        new_w = round(w * TARGET_SIZE / h)
                    im_resized = im.resize((new_w, new_h), Image.LANCZOS)
                    im_resized.save(dst, format="PNG", optimize=True)
                    return
        except Exception as e:
            print(f"   ! resize failed for {src.name}: {e} — copying raw")
    shutil.copy2(src, dst)


def resize_existing(root: Path, target_size: int) -> int:
    """One-shot resize every PNG under root that exceeds target_size."""
    if not HAS_PIL:
        print("PIL not available — skipping in-place resize.")
        return 0
    count = 0
    for p in root.rglob("*.png"):
        try:
            with Image.open(p) as im:
                w, h = im.size
                if w <= target_size and h <= target_size:
                    continue
                if w >= h:
                    new_w = target_size
                    new_h = round(h * target_size / w)
                else:
                    new_h = target_size
                    new_w = round(w * target_size / h)
                im_resized = im.resize((new_w, new_h), Image.LANCZOS)
            im_resized.save(p, format="PNG", optimize=True)
            count += 1
        except Exception as e:
            print(f"   ! resize failed for {p}: {e}")
    return count


def migrate_legacy_layout() -> int:
    """Move pre-existing flat files (e.g. pets/baby_neutral.png) into the
    new per-character layout (pets/baby/neutral.png). Returns count moved."""
    moved = 0
    for category, (plural, plural_dir) in CAT_INFO.items():
        if category in ("bait", "location"):
            continue   # stay flat
        if not plural_dir.exists():
            continue
        for png in list(plural_dir.glob("*.png")):
            stem = png.stem
            base, suffix, frame_num = parse_stem(stem)
            if not base or not suffix:
                continue
            char_dir = plural_dir / base
            if frame_num is not None:
                target = char_dir / "anims" / suffix / f"f{frame_num}.png"
            else:
                target = char_dir / f"{suffix}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_mtime >= png.stat().st_mtime:
                png.unlink()  # already up-to-date, remove stray flat copy
                moved += 1
                continue
            shutil.move(str(png), target)
            moved += 1
    return moved


def main() -> int:
    global PIXELIZE_ENABLED, PIXELIZE_PX, PIXELIZE_COLORS, DST, SRC, CAT_INFO
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None,
                        help="Override the sprites root (default static/sprites). Point at a "
                             "project's sprites dir (e.g. static/projects/<id>/sprites) to build "
                             "that project's manifest. Use with --manifest-only.")
    parser.add_argument("--resize-existing", action="store_true",
                        help="Also resize every existing PNG under static/sprites/ "
                             f"that exceeds {TARGET_SIZE}px to {TARGET_SIZE}px.")
    parser.add_argument("--manifest-only", action="store_true",
                        help="Skip importing from comfyui/output entirely; only walk "
                             "static/sprites/ and rebuild manifest.json. Use this with "
                             "the ChatGPT sheet workflow so deleted frames never get "
                             "resurrected from stale ComfyUI output.")
    parser.add_argument("--pixelize", action="store_true",
                        help=f"Run every imported PNG through mosaic pixelization "
                             f"({PIXELIZE_PX}px grid, {PIXELIZE_COLORS} colors, "
                             "NEAREST upscale) before saving. Use for the sprite-studio "
                             "(Wan-Animate / FLUX) output pipeline.")
    parser.add_argument("--pixelize-px", type=int, default=PIXELIZE_PX,
                        help=f"Logical pixel grid size when --pixelize is on. Default {PIXELIZE_PX}.")
    parser.add_argument("--pixelize-colors", type=int, default=PIXELIZE_COLORS,
                        help=f"Palette size when --pixelize is on. Default {PIXELIZE_COLORS}.")
    args = parser.parse_args()

    # --root: retarget the sprites root (for per-project manifests). Everything
    # downstream (CAT_INFO dirs, manifest output path) follows DST.
    if args.root:
        DST = Path(args.root).resolve()
        SRC = DST.parent / "_comfyui_output_unused"   # import is skipped in --manifest-only
        CAT_INFO = {
            "pet":      ("pets",      DST / "pets"),
            "monster":  ("monsters",  DST / "monsters"),
            "boss":     ("bosses",    DST / "bosses"),
            "bait":     ("baits",     DST / "baits"),
            "location": ("locations", DST / "locations"),
        }

    PIXELIZE_ENABLED = bool(args.pixelize)
    PIXELIZE_PX = args.pixelize_px
    PIXELIZE_COLORS = args.pixelize_colors
    if PIXELIZE_ENABLED and not HAS_PIXELIZE:
        print("! --pixelize requested but pixelize.py could not be imported.", file=sys.stderr)
        return 2

    if not args.manifest_only and not SRC.exists():
        print(f"source not found: {SRC}", file=sys.stderr)
        return 2
    for _, (_, plural_dir) in CAT_INFO.items():
        plural_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest_only:
        print("Manifest-only mode: skipping ComfyUI output import "
              "(static/sprites is the source of truth).")
    else:
        # 1) Migrate any legacy flat files first
        n_migrated = migrate_legacy_layout()
        if n_migrated:
            print(f"Migrated {n_migrated} legacy flat files into per-character folders.")

        # 1b) Optionally resize anything in static/sprites/ that's still oversize
        if args.resize_existing:
            n_resized = resize_existing(DST, TARGET_SIZE)
            if n_resized:
                print(f"Resized {n_resized} existing oversize PNGs to ≤{TARGET_SIZE}px.")

        # 2) Copy fresh ComfyUI outputs into the new layout (auto-downscale during copy).
        # Sort newest first so when multiple `_00001`, `_00002`, ... exist for the
        # same logical frame, the most recent generation wins deterministically.
        counts: dict[str, int] = {}
        pngs = sorted(SRC.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        seen_targets: set[Path] = set()
        for png in pngs:
            parsed = parse_output_name(png.name)
            if not parsed:
                continue
            category, stem = parsed
            target = route_target(category, stem)
            if target is None or target in seen_targets:
                continue
            seen_targets.add(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            _import_png(png, target)
            counts[category] = counts.get(category, 0) + 1

        print("Copied (newly imported from ComfyUI output):")
        for cat, n in sorted(counts.items()):
            print(f"  {cat}: {n} files → {CAT_INFO[cat][1]}")
        if not counts:
            print("  (nothing new from ComfyUI output/)")

    # 3) Build manifest: walk the new layout
    manifest: dict = {}
    for category, (plural, plural_dir) in CAT_INFO.items():
        if category in ("bait", "location"):
            files = sorted(p.stem for p in plural_dir.glob("*.png"))
            manifest[plural] = {"flat": True, "files": files}
            continue
        chars: dict[str, dict] = {}
        if plural_dir.exists():
            for char_dir in sorted(plural_dir.iterdir()):
                if not char_dir.is_dir():
                    continue
                statics = sorted(p.stem for p in char_dir.glob("*.png"))
                anims_dir = char_dir / "anims"
                animations: dict[str, int] = {}
                if anims_dir.exists():
                    for scen_dir in sorted(anims_dir.iterdir()):
                        if not scen_dir.is_dir():
                            continue
                        frames = [p for p in scen_dir.glob("f*.png")]
                        # Max frame number from filenames `fN.png`
                        max_n = 0
                        for f in frames:
                            m = re.match(r"^f(\d+)$", f.stem)
                            if m:
                                max_n = max(max_n, int(m.group(1)))
                        if max_n > 0:
                            animations[scen_dir.name] = max_n
                chars[char_dir.name] = {
                    "statics": statics,
                    "animations": animations,
                }
        manifest[plural] = {"flat": False, "characters": chars}

    out_path = DST / "manifest.json"
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nManifest written: {out_path}")
    # Summary
    for plural, data in manifest.items():
        if data.get("flat"):
            print(f"  {plural:10s}  flat-files={len(data['files']):3d}")
        else:
            chars = data.get("characters", {})
            n_static = sum(len(c["statics"]) for c in chars.values())
            n_anim_scen = sum(len(c["animations"]) for c in chars.values())
            n_anim_frames = sum(sum(c["animations"].values()) for c in chars.values())
            print(f"  {plural:10s}  chars={len(chars):3d}  statics={n_static:3d}  "
                  f"anim_scenarios={n_anim_scen:3d}  anim_frames={n_anim_frames:4d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
