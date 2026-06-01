"""
Sprite Atlas — a standalone, multi-project sprite/animation asset manager + editor.

A small FastAPI server + a single-page web UI (static/atlas.html) for organizing
and editing frame-based sprite animations across multiple game projects:

  • Multi-project workspace + a shared library (each project = a folder of sprites)
  • Import a folder of frames  → animation (grouped by sub-folder)
  • Slice a sprite sheet        → frames → animation
  • Frame manager               → reorder / delete / duplicate / add frames
  • Pixel editor                → brush / eraser / fill / eyedropper, onion-skin
  • Transform tuning            → scale / offset / frame range / edge-fade
  • (optional) AI tools          → Flux masked inpaint + InSPyReNet background removal
                                    — needs a local ComfyUI (:8188) and models;
                                    everything else works without it.

Run:
    pip install -r requirements.txt
    python server.py
    # open http://127.0.0.1:8000/

Layout under static/:
    projects/<id>/sprites/<category>/<char>/anims/<action>/f1.png..fN.png
    shared/sprites/...            (the shared library)
    <base>/manifest.json          (per-project, auto-built)
Registry: data/projects.json

MIT licensed. Extracted from a hobby game project; the core has no heavy deps.
"""
from __future__ import annotations

import base64 as _b64
import json
import os
import re as _re
import subprocess as _subprocess
import sys
import threading
import uuid as _uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = BASE / "data"
AI_DIR = BASE / "ai"
TOOLS = BASE / "tools"
PROJECTS_FILE = DATA / "projects.json"
TUNING_FILE = DATA / "sprite_tuning.json"
ORIG_BACKUPS = DATA / "orig_backups"
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")

CATEGORIES = ["pets", "monsters", "bosses", "baits", "locations"]
CHAR_CATEGORIES = ("pets", "monsters", "bosses")
# Singular tuning category ("pet"/"monster") → plural sprite folder.
_CAT_PLURAL = {"pet": "pets", "monster": "monsters", "boss": "bosses"}

for d in (STATIC, DATA, AI_DIR, TOOLS):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Sprite Atlas")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def _no_cache_static(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/")
@app.get("/atlas")
def atlas():
    return FileResponse(STATIC / "atlas.html")


# ---------------------------------------------------------------------------
# Project registry + manifests
# ---------------------------------------------------------------------------
def _default_registry() -> dict:
    return {"active": "demo", "projects": [
        {"id": "demo", "name": "Demo", "base": "projects/demo/sprites"},
        {"id": "shared", "name": "Shared Library", "base": "shared/sprites", "shared": True},
    ]}


def _load_projects() -> dict:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    reg = _default_registry()
    _save_projects(reg)
    return reg


def _save_projects(reg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def _project_by_id(reg: dict, pid: str) -> dict | None:
    for p in reg.get("projects", []):
        if p.get("id") == pid:
            return p
    return None


def _active_base(reg: dict | None = None) -> str:
    reg = reg or _load_projects()
    p = _project_by_id(reg, reg.get("active", ""))
    return p["base"] if p else "projects/demo/sprites"


def _read_manifest_for_base(base: str) -> dict:
    mp = STATIC / base / "manifest.json"
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _rebuild_manifest_for_base(base: str) -> None:
    sprites_dir = STATIC / base
    sprites_dir.mkdir(parents=True, exist_ok=True)
    _subprocess.run([sys.executable, str(TOOLS / "manifest_build.py"),
                     "--manifest-only", "--root", str(sprites_dir)],
                    cwd=str(BASE), capture_output=True, text=True)


def _safe_seg(s: str) -> str:
    s = (s or "").strip().lower().replace(" ", "_")
    return _re.sub(r"[^a-z0-9_\-]", "", s)[:48]


@app.get("/api/projects")
def api_projects():
    return _load_projects()


class NewProjectReq(BaseModel):
    id: str
    name: str | None = None


@app.post("/api/projects")
def api_create_project(req: NewProjectReq):
    pid = _safe_seg(req.id)
    if not pid or not _re.match(r"^[a-z0-9]", pid):
        raise HTTPException(400, "id must be a slug starting with a-z or 0-9")
    reg = _load_projects()
    if _project_by_id(reg, pid):
        raise HTTPException(409, f"project '{pid}' already exists")
    base = f"projects/{pid}/sprites"
    for cat in CATEGORIES:
        (STATIC / base / cat).mkdir(parents=True, exist_ok=True)
    _rebuild_manifest_for_base(base)
    entry = {"id": pid, "name": (req.name or pid).strip() or pid, "base": base}
    reg["projects"].append(entry)
    _save_projects(reg)
    return {"ok": True, "project": entry, "projects": reg["projects"]}


class ProjectIdReq(BaseModel):
    id: str


@app.post("/api/projects/active")
def api_set_active(req: ProjectIdReq):
    reg = _load_projects()
    if not _project_by_id(reg, req.id):
        raise HTTPException(404, f"unknown project: {req.id}")
    reg["active"] = req.id
    _save_projects(reg)
    return {"ok": True, "active": req.id}


@app.post("/api/projects/rebuild")
def api_rebuild_project(req: ProjectIdReq):
    reg = _load_projects()
    p = _project_by_id(reg, req.id)
    if not p:
        raise HTTPException(404, f"unknown project: {req.id}")
    _rebuild_manifest_for_base(p["base"])
    return {"ok": True, "id": req.id, "manifest": _read_manifest_for_base(p["base"])}


@app.get("/api/atlas/manifest")
def api_atlas_manifest(project: str | None = None):
    """Active project's assets merged with the shared library. Each character
    gets `_base` (its URL prefix) and `_shared` (came from the shared library)."""
    reg = _load_projects()
    pid = project or reg.get("active") or "demo"
    proj = _project_by_id(reg, pid)
    if not proj:
        raise HTTPException(404, f"unknown project: {pid}")
    proj_base_url = "/static/" + proj["base"]
    pm = _read_manifest_for_base(proj["base"])
    out: dict = {}
    for cat in CHAR_CATEGORIES:
        merged = {}
        for cid, c in (pm.get(cat, {}) or {}).get("characters", {}).items():
            e = dict(c); e["_base"] = proj_base_url; e["_shared"] = False
            merged[cid] = e
        out[cat] = {"flat": False, "characters": merged}
    for cat in ("baits", "locations"):
        fc = dict(pm.get(cat, {}) or {"flat": True, "files": []})
        fc["_base"] = proj_base_url
        out[cat] = fc
    if not proj.get("shared"):
        shared = _read_manifest_for_base("shared/sprites")
        for cat in CHAR_CATEGORIES:
            for cid, c in (shared.get(cat, {}) or {}).get("characters", {}).items():
                if cid in out[cat]["characters"]:
                    continue
                e = dict(c); e["_base"] = "/static/shared/sprites"; e["_shared"] = True
                out[cat]["characters"][cid] = e
    return {"project": pid, "name": proj.get("name", pid), "base": proj_base_url,
            "active": reg.get("active"), "projects": reg["projects"], "manifest": out}


# ---------------------------------------------------------------------------
# Frame paths (project-aware) + original-frame backups
# ---------------------------------------------------------------------------
def _frame_dir(category: str, base: str, action: str, proj_base: str | None = None) -> Path:
    plural = _CAT_PLURAL.get(category, category if category in CATEGORIES else "pets")
    root = proj_base or _active_base()
    return STATIC / root / plural / _safe_seg(base) / "anims" / _safe_seg(action)


def _frame_path(category: str, base: str, action: str, frame: int, proj_base: str | None = None) -> Path:
    return _frame_dir(category, base, action, proj_base) / f"f{int(frame)}.png"


def _decode_dataurl_png(durl: str) -> bytes:
    if "," in durl and durl.strip().lower().startswith("data:"):
        durl = durl.split(",", 1)[1]
    return _b64.b64decode(durl)


def _orig_backup(src: Path) -> None:
    """Back up a frame once (before the first edit) so it can be restored."""
    try:
        rel = src.resolve().relative_to(STATIC.resolve())
    except Exception:
        rel = Path(src.name)
    dst = ORIG_BACKUPS / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            import shutil
            shutil.copy2(src, dst)
        except Exception:
            pass


class SaveFramesReq(BaseModel):
    project: str
    category: str = "pets"
    char: str
    action: str
    frames: list[str]
    replace: bool = True


@app.post("/api/project/save-frames")
def api_save_frames(req: SaveFramesReq):
    """Write PNG frames (data-URLs) into a project's action folder, renumbered
    f1..fN, then rebuild that project's manifest. Foundation for import / sheet
    slicing / frame manager / pixel editor saves."""
    reg = _load_projects()
    p = _project_by_id(reg, req.project)
    if not p:
        raise HTTPException(404, f"unknown project: {req.project}")
    cat = _CAT_PLURAL.get(req.category, req.category if req.category in CHAR_CATEGORIES else "pets")
    char, action = _safe_seg(req.char), _safe_seg(req.action)
    if not char or not action:
        raise HTTPException(400, "char and action required (a-z 0-9 _ -)")
    if not req.frames:
        raise HTTPException(400, "no frames provided")
    dest = STATIC / p["base"] / cat / char / "anims" / action
    dest.mkdir(parents=True, exist_ok=True)
    if req.replace:
        for old in dest.glob("f*.png"):
            try: old.unlink()
            except Exception: pass
    for i, durl in enumerate(req.frames, 1):
        try:
            (dest / f"f{i}.png").write_bytes(_decode_dataurl_png(durl))
        except Exception as e:
            raise HTTPException(400, f"frame {i} failed: {e}")
    _rebuild_manifest_for_base(p["base"])
    return {"ok": True, "saved": len(req.frames),
            "path": f"{p['base']}/{cat}/{char}/anims/{action}"}


@app.post("/api/rename-actions")
def rename_actions(body: dict):
    """Rename action folders under <activeProject>/<plural>/<char>/anims/.
    body = {category, base(char), mapping:{old:new}}."""
    import shutil
    category = body.get("category", "pet")
    char = body.get("base") or body.get("char") or ""
    mapping = body.get("mapping", {}) or {}
    plural = _CAT_PLURAL.get(category, "pets")
    anims = STATIC / _active_base() / plural / _safe_seg(char) / "anims"
    if not anims.is_dir():
        raise HTTPException(404, f"no anims dir for {char}")
    pending = {k: v for k, v in mapping.items() if k != v}
    if not pending:
        return {"ok": True, "renamed": 0}
    tmp = {}
    for src in pending:
        sp = anims / src
        if not sp.is_dir():
            raise HTTPException(400, f"missing source: {src}")
        t = anims / f"__rn_{src}__"; sp.rename(t); tmp[src] = t
    moved = []
    for src, dst in pending.items():
        dp = anims / _safe_seg(dst)
        if dp.exists():
            raise HTTPException(409, f"destination exists: {dst}")
        tmp[src].rename(dp); moved.append({"from": src, "to": dst})
    _rebuild_manifest_for_base(_active_base())
    return {"ok": True, "renamed": len(moved), "moves": moved}


# ---------------------------------------------------------------------------
# Transform tuning (scale / offset / frame range / removed frames / edge-fade)
# ---------------------------------------------------------------------------
def _load_tuning() -> dict:
    if TUNING_FILE.exists():
        try:
            return json.loads(TUNING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_tuning(t: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    TUNING_FILE.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/sprite-tuning")
def get_sprite_tuning():
    return _load_tuning()


class TuningReq(BaseModel):
    category: str
    base: str
    action: str
    scale: float | None = None
    offset_x: int | None = None
    offset_y: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    frame_total: int | None = None
    frames_removed: list[int] | None = None
    edge_fade: dict | None = None


@app.post("/api/sprite-tuning")
def set_sprite_tuning(req: TuningReq):
    t = _load_tuning()
    t.setdefault(req.category, {})
    t[req.category].setdefault(req.base, {})
    entry = t[req.category][req.base].setdefault(req.action, {})
    if req.scale is not None:
        entry.pop("scale", None) if abs(req.scale - 1.0) < 0.001 else entry.__setitem__("scale", round(float(req.scale), 3))
    if req.offset_x is not None:
        entry.pop("offset_x", None) if int(req.offset_x) == 0 else entry.__setitem__("offset_x", int(req.offset_x))
    if req.offset_y is not None:
        entry.pop("offset_y", None) if int(req.offset_y) == 0 else entry.__setitem__("offset_y", int(req.offset_y))
    if req.frame_start is not None:
        entry.pop("frame_start", None) if req.frame_start <= 1 else entry.__setitem__("frame_start", int(req.frame_start))
    if req.frame_end is not None:
        if req.frame_total is not None and int(req.frame_end) >= int(req.frame_total):
            entry.pop("frame_end", None)
        else:
            entry["frame_end"] = int(req.frame_end)
    if req.frames_removed is not None:
        clean = sorted({int(f) for f in req.frames_removed if int(f) >= 1})
        entry["frames_removed"] = clean if clean else entry.pop("frames_removed", None)
        if not clean:
            entry.pop("frames_removed", None)
    if req.edge_fade is not None:
        ef = req.edge_fade or {}
        typ, amt = str(ef.get("type", "none")), int(ef.get("amount", 0) or 0)
        if typ in ("rect", "circle") and amt > 0:
            entry["edge_fade"] = {"type": typ, "amount": max(0, min(60, amt))}
        else:
            entry.pop("edge_fade", None)
    if not entry:
        t[req.category][req.base].pop(req.action, None)
    _save_tuning(t)
    return {"ok": True, "tuning": t[req.category][req.base].get(req.action, {})}


# ---------------------------------------------------------------------------
# Restore original frames (undo AI edits / matte)
# ---------------------------------------------------------------------------
class RestoreReq(BaseModel):
    category: str
    base: str
    action: str
    frame: int | None = None
    scope: str = "this"   # "this" | "all"


@app.get("/api/frame/original")
def frame_original(category: str, base: str, action: str, frame: int):
    src = _frame_path(category, base, action, frame)
    try:
        rel = src.resolve().relative_to(STATIC.resolve())
        bk = ORIG_BACKUPS / rel
    except Exception:
        bk = None
    use = bk if (bk and bk.exists()) else (src if src.exists() else None)
    if not use:
        raise HTTPException(404, "no original")
    return {"data": "data:image/png;base64," + _b64.b64encode(use.read_bytes()).decode()}


@app.post("/api/frame/restore")
def frame_restore(req: RestoreReq):
    import shutil
    out = []
    frames = ([req.frame] if req.scope == "this" and req.frame else
              [int(p.stem[1:]) for p in sorted(_frame_dir(req.category, req.base, req.action).glob("f*.png"),
                                               key=lambda p: int(p.stem[1:]))])
    for fr in frames:
        src = _frame_path(req.category, req.base, req.action, fr)
        try:
            rel = src.resolve().relative_to(STATIC.resolve())
            bk = ORIG_BACKUPS / rel
        except Exception:
            continue
        if bk.exists():
            shutil.copy2(bk, src); out.append(fr)
    return {"ok": True, "restored": out}


# ---------------------------------------------------------------------------
# OPTIONAL AI tools — Flux masked inpaint + InSPyReNet matte.
# Need a local ComfyUI (COMFY_URL, default :8188) and the relevant models.
# Without ComfyUI these endpoints return 503 and the UI panels stay disabled.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(AI_DIR))
_flux_jobs: dict[str, dict] = {}
_matte_jobs: dict[str, dict] = {}
_MATTE_SCRIPT = AI_DIR / "matte_tool.py"


def _flux_mod():
    import flux_edit as _fe  # noqa
    _fe.COMFY_URL = COMFY_URL
    return _fe


def _comfy_input() -> Path:
    d = AI_DIR / "input"; d.mkdir(parents=True, exist_ok=True); return d


def _write_mask(mask_b64: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_decode_dataurl_png(mask_b64))


class FluxEditReq(BaseModel):
    category: str
    base: str
    action: str
    frame: int
    prompt: str
    mask: str
    seed: int | None = None
    model: str = "kontext"


@app.get("/api/flux/status")
def flux_status():
    try:
        return {"running": _flux_mod().comfy_up()}
    except Exception as e:
        return {"running": False, "error": str(e)}


@app.post("/api/flux/edit")
def flux_edit_preview(req: FluxEditReq):
    fe = _flux_mod()
    if not fe.comfy_up():
        raise HTTPException(503, f"ComfyUI not running on {COMFY_URL}")
    src = _frame_path(req.category, req.base, req.action, req.frame)
    if not src.exists():
        raise HTTPException(404, f"frame not found: {src.name}")
    mask_png = _comfy_input() / f"editmask_{_uuid.uuid4().hex[:8]}.png"
    _write_mask(req.mask, mask_png)
    try:
        out = fe.edit_frame(src, mask_png, req.prompt, seed=int(req.seed or 42), out_prefix="atlas_edit")
    except Exception as e:
        raise HTTPException(500, f"flux edit failed: {e}")
    return {"preview": "data:image/png;base64," + _b64.b64encode(out.read_bytes()).decode(), "out_file": out.name}


class FluxApplyReq(FluxEditReq):
    scope: str = "this"
    out_file: str | None = None


@app.post("/api/flux/apply")
def flux_apply(req: FluxApplyReq):
    import shutil
    fe = _flux_mod()
    if not fe.comfy_up():
        raise HTTPException(503, f"ComfyUI not running on {COMFY_URL}")
    src = _frame_path(req.category, req.base, req.action, req.frame)
    if not src.exists():
        raise HTTPException(404, "frame not found")
    seed = int(req.seed or 42)
    if req.scope == "this":
        out = None
        if req.out_file:
            cand = fe.COMFY_OUTPUT / req.out_file
            if cand.exists():
                out = cand
        if out is None:
            mask_png = _comfy_input() / f"applymask_{_uuid.uuid4().hex[:8]}.png"
            _write_mask(req.mask, mask_png)
            out = fe.edit_frame(src, mask_png, req.prompt, seed=seed, out_prefix="atlas_apply")
        _orig_backup(src); shutil.copy2(out, src)
        return {"applied": [int(req.frame)], "count": 1}
    if req.scope == "all":
        mask_png = _comfy_input() / f"applymask_{_uuid.uuid4().hex[:8]}.png"
        _write_mask(req.mask, mask_png)
        frames = sorted(src.parent.glob("f*.png"), key=lambda p: int(p.stem[1:]))
        job_id = _uuid.uuid4().hex[:12]
        _flux_jobs[job_id] = {"state": "running", "done": 0, "total": len(frames), "errors": []}
        prompt_text, j = req.prompt, _flux_jobs[job_id]

        def _run():
            for fp in frames:
                try:
                    o = fe.edit_frame(fp, mask_png, prompt_text, seed=seed, out_prefix="atlas_all")
                    _orig_backup(fp); shutil.copy2(o, fp)
                except Exception as e:
                    j["errors"].append(f"{fp.name}: {e}")
                j["done"] += 1
            j["state"] = "done"
        threading.Thread(target=_run, daemon=True).start()
        return {"job_id": job_id, "total": len(frames)}
    raise HTTPException(400, "scope must be this|all")


@app.get("/api/flux/job/{job_id}")
def flux_job(job_id: str):
    j = _flux_jobs.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j


class MatteReq(BaseModel):
    category: str
    base: str
    action: str
    frame: int


def _has_matte() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("transparent_background") is not None
    except Exception:
        return False


@app.get("/api/matte/status")
def matte_status():
    return {"available": _has_matte()}


@app.post("/api/matte/preview")
def matte_preview(req: MatteReq):
    if not _has_matte():
        raise HTTPException(503, "matte needs `pip install transparent-background`")
    src = _frame_path(req.category, req.base, req.action, req.frame)
    if not src.exists():
        raise HTTPException(404, f"frame not found: {src.name}")
    out = _comfy_input() / f"matteprev_{_uuid.uuid4().hex[:8]}.png"
    r = _subprocess.run([sys.executable, str(_MATTE_SCRIPT), "--src", str(src), "--out", str(out)],
                        cwd=str(BASE), capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        raise HTTPException(500, f"matte failed: {(r.stderr or r.stdout)[-300:]}")
    return {"preview": "data:image/png;base64," + _b64.b64encode(out.read_bytes()).decode(), "out_file": out.name}


class MatteApplyReq(MatteReq):
    scope: str = "this"
    out_file: str | None = None


@app.post("/api/matte/apply")
def matte_apply(req: MatteApplyReq):
    import shutil
    if not _has_matte():
        raise HTTPException(503, "matte needs `pip install transparent-background`")
    src = _frame_path(req.category, req.base, req.action, req.frame)
    if not src.exists():
        raise HTTPException(404, "frame not found")
    if req.scope == "this":
        _orig_backup(src)
        if req.out_file:
            cand = _comfy_input() / req.out_file
            if cand.exists():
                shutil.copy2(cand, src)
                return {"applied": [int(req.frame)], "count": 1}
        out = _comfy_input() / f"matteone_{_uuid.uuid4().hex[:8]}.png"
        r = _subprocess.run([sys.executable, str(_MATTE_SCRIPT), "--src", str(src), "--out", str(out)],
                            cwd=str(BASE), capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not out.exists():
            raise HTTPException(500, f"matte failed: {(r.stderr or r.stdout)[-300:]}")
        shutil.copy2(out, src)
        return {"applied": [int(req.frame)], "count": 1}
    if req.scope == "all":
        d = _frame_dir(req.category, req.base, req.action)
        for fp in d.glob("f*.png"):
            _orig_backup(fp)
        job_id = _uuid.uuid4().hex[:12]
        status_path = _comfy_input() / f"mattejob_{job_id}.json"
        _matte_jobs[job_id] = {"state": "running", "done": 0, "total": 0, "status_path": str(status_path)}
        _subprocess.Popen([sys.executable, str(_MATTE_SCRIPT), "--indir", str(d), "--status", str(status_path)],
                          cwd=str(BASE))
        return {"job_id": job_id}
    raise HTTPException(400, "scope must be this|all")


@app.get("/api/matte/job/{job_id}")
def matte_job(job_id: str):
    j = _matte_jobs.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    sp = Path(j.get("status_path", ""))
    if sp.exists():
        try:
            j.update(json.loads(sp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return j


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Sprite Atlas → http://127.0.0.1:{port}/  (Ctrl+C to quit)")
    uvicorn.run(app, host="127.0.0.1", port=port)
