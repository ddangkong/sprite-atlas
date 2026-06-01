# Setting up the optional AI tools

The core atlas needs only `fastapi`, `uvicorn`, `pillow` and works with **no AI
setup at all** — the 누끼 (background removal) and 🪄 Flux (masked inpaint) panels
simply stay disabled until their dependencies are present.

This page is for turning those two on. The easiest path is the bootstrap script.

## One command

```powershell
# Windows (PowerShell)
.\setup.ps1 -All
```

```bash
# macOS / Linux
./setup.sh --all
```

`-All` / `--all` does everything: core deps → `transparent-background` (누끼) →
clone ComfyUI into `vendor/ComfyUI` with its own venv → download the four Flux
Kontext models (~17 GB) into that clone. Pick à la carte with `-Matte`/`-Comfy`/
`-Models` (PowerShell) or `--matte`/`--comfy`/`--models` (bash).

> Models and ComfyUI are **not** committed to this repo (multi-GB, separately
> licensed). The script downloads them; `vendor/` is git-ignored.

## What each piece is

### 누끼 / matte — InSPyReNet background removal
Just a pip package, no GPU model server required:
```
pip install transparent-background
```
First run downloads the InSPyReNet weights (~hundreds of MB) automatically and
runs on CPU. After this, `/api/matte/status` reports `available: true` and the
🪄 button lights up.

### 🪄 Flux masked inpaint — needs a local ComfyUI
`ai/flux_edit.py` is only an **HTTP client**: it talks to a ComfyUI server you run
yourself (default `http://127.0.0.1:8188`). The atlas reads `COMFY_URL` to find it;
if ComfyUI isn't up, `/api/flux/*` returns **503** and the panel stays disabled.

The workflow `ai/workflows/flux_kontext_consistency.json` references these four
models — place them under your ComfyUI clone exactly like this:

| File | ComfyUI folder | Loader node | ~Size |
|---|---|---|---|
| `flux1-dev-kontext_fp8_scaled.safetensors` | `models/diffusion_models/` | UNETLoader | 11.9 GB |
| `t5xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` | DualCLIPLoader | 4.9 GB |
| `clip_l.safetensors` | `models/text_encoders/` | DualCLIPLoader | 246 MB |
| `ae.safetensors` | `models/vae/` | VAELoader | 335 MB |

Source (public, no gate): the Comfy-Org packaged build
[`Comfy-Org/flux1-kontext-dev_ComfyUI`](https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI)
under `split_files/…`. The setup script pulls them from there.

## Running it after setup

ComfyUI and the atlas are two processes. Start ComfyUI first:

```powershell
# Windows
vendor\ComfyUI\venv\Scripts\python.exe vendor\ComfyUI\main.py --listen 127.0.0.1 --port 8188
```
```bash
# macOS / Linux
vendor/ComfyUI/venv/bin/python vendor/ComfyUI/main.py --listen 127.0.0.1 --port 8188
```

Then start the atlas pointed at it:

```powershell
$env:COMFY_URL = 'http://127.0.0.1:8188'; python server.py
```
```bash
COMFY_URL=http://127.0.0.1:8188 python server.py
```

Open <http://127.0.0.1:8000/>, pick a frame, and the 🪄 부분 수정 panel should be
active. `GET /api/flux/status` is a quick health check.

## Notes & troubleshooting

- **GPU vs CPU.** ComfyUI's default `torch` from PyPI is **CPU-only**; Flux runs
  but is slow. For NVIDIA, reinstall torch in the ComfyUI venv with a CUDA wheel:
  ```
  vendor\ComfyUI\venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
  ```
  (swap `cu124` for your CUDA version). InSPyReNet matte is CPU-fine either way.
- **401 / 403 on a model download.** The Comfy-Org build is ungated, but if a
  mirror ever requires auth: `pip install huggingface_hub` then `huggingface-cli
  login` and re-run with `-Models` / `--models` (downloads resume).
- **Different model location.** If you already have ComfyUI elsewhere, skip
  `-Comfy`/`-Models`, drop the four files into your existing `models/…` folders,
  and just point `COMFY_URL` at your server.
- **VRAM.** The fp8 Kontext build targets ~12 GB cards. Lower-VRAM GPUs may need
  ComfyUI's `--lowvram` flag.
