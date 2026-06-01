# Sprite Atlas — developer notes

Architecture + status + where things live, for picking the project back up.
(User-facing docs are in [README.md](README.md).)

## Run

```bash
pip install -r requirements.txt
python server.py            # http://127.0.0.1:8000/   (PORT env to change)
COMFY_URL=http://127.0.0.1:8188 python server.py   # enable Flux inpaint
```

`server.py` resolves all paths from `__file__`, so it runs from any cwd.

## Architecture

- **`server.py`** — FastAPI. All endpoints are **project-aware**: they resolve
  frame paths via the *active* project's `base` (from `data/projects.json`), not
  a fixed root. Key helpers: `_load_projects`/`_save_projects`, `_active_base`,
  `_read_manifest_for_base`, `_rebuild_manifest_for_base` (shells
  `tools/manifest_build.py --root <dir>`), `_frame_path`/`_frame_dir`,
  `_orig_backup` (one-time backup to `data/orig_backups/` before edits),
  `_safe_seg` (path-segment sanitizer).
- **`static/atlas.html`** — the whole UI (vanilla JS, no build). Globals:
  `PROJECTS`, `ACTIVE_PROJECT`, `CHAR_BASE` (per-char URL base), `manifest`,
  `monsters`, `tuning`. URLs are built via `spriteRoot(folder, base)`. Init IIFE
  at the bottom: loads `/api/projects` → `loadManifest()` (builds CHAR_BASE) →
  `loadMonsters()` (reads manifest) → render. **`loadManifest` must finish before
  `loadMonsters`** (monsters read the manifest).
- **`tools/manifest_build.py`** — walks a sprites root, emits `manifest.json`
  (`{pets,monsters,bosses}.characters[].animations{action:frameCount}` + flat
  baits/locations). `--root <dir>` targets any project. `--manifest-only` skips
  any import step.
- **`ai/flux_edit.py`** — talks to ComfyUI over HTTP (`/prompt`, `/upload/image`,
  `/view`), injects a mask-composite so only the painted region changes. Loads
  `ai/workflows/flux_kontext_consistency.json`. Pure stdlib.
- **`ai/matte_tool.py`** — InSPyReNet (`transparent_background`) background
  removal; CLI invoked as a subprocess by the server (`--src/--out`, `--indir/--status`).

## Endpoints

Projects: `GET /api/projects`, `POST /api/projects` (create), `POST
/api/projects/active`, `POST /api/projects/rebuild`. Assets: `GET
/api/atlas/manifest?project=<id>` (project + shared merged, each char tagged
`_base`/`_shared`), `POST /api/project/save-frames` (write f1..fN + rebuild),
`POST /api/rename-actions`. Tuning: `GET|POST /api/sprite-tuning`. Restore:
`GET /api/frame/original`, `POST /api/frame/restore`. Optional AI: `GET
/api/flux/status`, `POST /api/flux/edit|apply`, `GET /api/flux/job/{id}`, `GET
/api/matte/status`, `POST /api/matte/preview|apply`, `GET /api/matte/job/{id}`.

The save foundation is `/api/project/save-frames` — folder-import-save, sheet
slicing, frame manager, and pixel editor all POST their frames (data-URL PNGs)
to it.

## UI features (all wired in atlas.html)

| Feature | Entry point | Notes |
|---|---|---|
| Multi-project switch | project bar dropdown | `switchProject` / `reloadProject` |
| New project | `+ 새 프로젝트` | name (any lang) → auto-slug id (`slugifyProjectId`) |
| Folder import (preview) | `📂 폴더 불러오기` | `<input webkitdirectory>`, grouped by sub-folder, blob preview, `↩ 되돌리기` rollback |
| Save import → project | `💾 프로젝트에 저장` | `saveImportedToProject` |
| Sheet slicing | `✂ 시트 자르기` | `#sliceOverlay`, grid/cell, blank-skip |
| Frame manager | card `🎞` | `window.openFrameManager`, drag-reorder/del/dup/add |
| Pixel editor | card `🖌` | `window.openPixelEditor`, brush/eraser/fill/pick, undo/onion |
| Transform tuning | click a card | scale/offset/frame-range/edge-fade |

## Done / verified

Backend + UI verified standalone (no console errors, demo renders, project
switch, save round-trips to disk + manifest). Core deps only:
fastapi/uvicorn/pillow. AI degrades gracefully (503 / disabled) without ComfyUI
or transparent-background.

## TODO / next

- **i18n** — UI strings are Korean; extract to make English/other locales easy.
- **Edit other categories** — save/edit is currently `pets`-only; generalize to
  monsters/bosses (atlas tabs already render them read-only).
- **Shared ↔ project copy UI** — currently move folders manually.
- **Action rename/delete UI** — `/api/rename-actions` exists; no UI button yet.
- **AI project-awareness is done** server-side (active project base); only tested
  for the no-ComfyUI graceful path here — verify Flux/matte end-to-end on a GPU box.
- Richer demo assets; screenshots/GIF in the README.

## Provenance

Extracted from a hobby tamagotchi game's in-repo asset/verification page
(`atlas.html`) + its FastAPI asset endpoints + ComfyUI helper scripts, then
decoupled from the game and generalized (project-aware paths, manifest-based
monster tab, generic branding). The game itself is unaffected.
