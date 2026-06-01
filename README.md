# 🗺 Sprite Atlas

A tiny, self-hosted **sprite / frame-animation asset tool** for game devs.
One FastAPI server + a single-page web UI to organize and edit frame-based
animations across multiple game projects — no build step, no database.

> Extracted from a hobby game project. The core has only three dependencies
> (FastAPI, Uvicorn, Pillow). The UI is currently in **Korean**.

![status](https://img.shields.io/badge/status-working-brightgreen) ![license](https://img.shields.io/badge/license-MIT-blue)

## What it does

- **Multi-project workspace + shared library** — each project is just a folder of
  sprites; switch between them, and a shared library merges into every project.
- **📂 Import a folder** of frame images → preview as an animation (grouped by
  sub-folder = action), then save into a project.
- **✂ Sheet slicing** — load one sprite sheet, slice by rows×columns or cell
  size (auto-skips blank cells) → frames → animation → save.
- **🎞 Frame manager** — reorder (drag), delete, duplicate, add frames per action.
- **🖌 Pixel editor** — brush / eraser / fill (flood) / eyedropper, color + size,
  undo/redo, onion-skin, frame navigation.
- **Transform tuning** — per-action scale / offset / frame range / removed
  frames / edge-fade, saved to `data/sprite_tuning.json`.
- **(optional) AI tools** — Flux masked inpaint + InSPyReNet background removal.
  These need extra setup (see below); **everything above works without them.**

Each project keeps its own `manifest.json` (auto-rebuilt), and the web UI plays
every action as a live flipbook.

## Quick start

```bash
pip install -r requirements.txt
python server.py
# open http://127.0.0.1:8000/
```

That's it. A small **Demo** project (a spinning diamond) is included so the UI
isn't empty on first run. Click **+ 새 프로젝트** (New Project) to make your own,
then drop sprite frames in and hit **↻ 새로고침** (Rebuild), or use Import / Sheet
slicing to bring frames in.

### Folder layout

```
static/
  projects/<id>/sprites/<category>/<char>/anims/<action>/f1.png … fN.png
  shared/sprites/...            # the shared library
  <base>/manifest.json          # per-project, auto-built
data/projects.json              # project registry  {active, projects[]}
data/sprite_tuning.json         # per-action transforms
```

`<category>` is `pets | monsters | bosses | baits | locations` (sensible
defaults — rename in the UI tabs if you like). Frames are PNGs named `f1..fN`.

## Optional: AI editing

Two AI features light up **only if you have them set up** — otherwise their
buttons stay disabled and the rest of the tool works fine.

- **🪄 Flux masked inpaint** (paint a region → AI redraws just that area).
  Needs a local [ComfyUI](https://github.com/comfyanonymous/ComfyUI) running and
  the Flux models referenced by `ai/workflows/flux_kontext_consistency.json`
  (e.g. a Flux Kontext / Fill checkpoint). Point the server at it:
  ```bash
  COMFY_URL=http://127.0.0.1:8188 python server.py
  ```
- **누끼 / matte** (one-click background removal) via
  [InSPyReNet](https://github.com/plemeri/transparent-background):
  ```bash
  pip install transparent-background
  ```

Models are **not** bundled (multi-GB, separately licensed). The AI code lives in
`ai/` and talks to ComfyUI over HTTP — see `ai/flux_edit.py` and `ai/matte_tool.py`.

## Project structure

```
server.py                 # FastAPI app — all asset endpoints (project-aware)
static/atlas.html         # the entire web UI (vanilla JS, no build)
tools/manifest_build.py   # scans a sprites folder → manifest.json (--root <dir>)
ai/flux_edit.py           # optional: ComfyUI Flux masked inpaint (HTTP)
ai/matte_tool.py          # optional: InSPyReNet background removal
ai/workflows/*.json       # ComfyUI workflow used by flux_edit
data/                     # registry + tuning (+ runtime backups, gitignored)
```

### Key API (for the curious)

`GET /api/projects` · `POST /api/projects` · `POST /api/projects/active` ·
`POST /api/projects/rebuild` · `GET /api/atlas/manifest?project=<id>` ·
`POST /api/project/save-frames` · `GET|POST /api/sprite-tuning` ·
`POST /api/frame/restore` · `GET /api/flux/status` · `POST /api/flux/edit|apply` ·
`GET /api/matte/status` · `POST /api/matte/preview|apply`.

## Notes / limitations

- UI is in Korean (PRs to internationalize welcome).
- Editing/saving writes to the **active project's** folder — switch projects
  before editing so you don't overwrite the wrong one.
- Save category is currently `pets`; other categories are view-only for now.

## License

MIT — see [LICENSE](LICENSE).
