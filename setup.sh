#!/usr/bin/env bash
# Sprite Atlas setup / bootstrap (macOS / Linux).
#
# Installs the core tool and, optionally, the AI extras:
#   core             FastAPI + Uvicorn + Pillow             (always)
#   --matte          InSPyReNet background removal (누끼)    (pip: transparent-background)
#   --comfy          clones ComfyUI into vendor/ + venv      (for Flux masked inpaint)
#   --models         downloads the 4 Flux Kontext models     (~17 GB, into the ComfyUI clone)
#   --all            = --matte --comfy --models
#
# ComfyUI and the Flux models are NOT bundled (multi-GB, separately licensed). This
# script fetches them and wires COMFY_URL so the atlas finds a locally-running
# ComfyUI. Without them the atlas still runs fully — the AI panels just stay disabled.
#
# Usage:
#   ./setup.sh                 # core only
#   ./setup.sh --matte         # core + one-click background removal
#   ./setup.sh --all           # core + matte + ComfyUI + Flux models (full AI)
#   ./setup.sh --comfy --models
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMFY_DIR="${COMFY_DIR:-$HERE/vendor/ComfyUI}"
COMFY_PORT="${COMFY_PORT:-8188}"
DO_MATTE=0; DO_COMFY=0; DO_MODELS=0

for arg in "$@"; do
  case "$arg" in
    --matte)  DO_MATTE=1 ;;
    --comfy)  DO_COMFY=1 ;;
    --models) DO_MODELS=1 ;;
    --all)    DO_MATTE=1; DO_COMFY=1; DO_MODELS=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 2 ;;
  esac
done
[ "$DO_MODELS" -eq 1 ] && DO_COMFY=1   # models live inside the ComfyUI clone

c_cyan='\033[36m'; c_green='\033[32m'; c_yellow='\033[33m'; c_gray='\033[90m'; c_off='\033[0m'
info() { printf "  ${c_gray}%s${c_off}\n" "$*"; }
step() { printf "\n${c_cyan}=== %s ===${c_off}\n" "$*"; }
ok()   { printf "  ${c_green}[OK] %s${c_off}\n" "$*"; }
warn() { printf "  ${c_yellow}[!] %s${c_off}\n" "$*"; }

# Resolve a python3.
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;assert sys.version_info[0]==3' >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Python 3 not found on PATH. Install it and re-run."; exit 1; }

# download URL -> dest (skip if a non-trivial file already exists)
get_file() {
  local url="$1" dest="$2" name; name="$(basename "$dest")"
  mkdir -p "$(dirname "$dest")"
  if [ -f "$dest" ] && [ "$(stat -f%z "$dest" 2>/dev/null || stat -c%s "$dest" 2>/dev/null || echo 0)" -gt 1000000 ]; then
    ok "$name already present - skipping"; return
  fi
  info "downloading $name ..."
  if command -v curl >/dev/null 2>&1; then
    curl -L --fail --retry 3 -C - -o "$dest" "$url" || { echo "download failed ($name). If 401/403, run 'huggingface-cli login'."; exit 1; }
  elif command -v wget >/dev/null 2>&1; then
    wget -c -O "$dest" "$url" || { echo "download failed ($name)."; exit 1; }
  else
    echo "need curl or wget to download models."; exit 1
  fi
  ok "$name"
}

printf "${c_gray}Sprite Atlas setup  (python=%s  repo=%s)${c_off}\n" "$PY" "$HERE"
info "extras: matte=$DO_MATTE comfy=$DO_COMFY models=$DO_MODELS"

# ----------------------------------------------------------------- core
step "Core dependencies"
"$PY" -m pip install -r "$HERE/requirements.txt"
ok "fastapi + uvicorn + pillow installed"

# ----------------------------------------------------------------- matte
if [ "$DO_MATTE" -eq 1 ]; then
  step "AI: background removal (InSPyReNet / 누끼)"
  "$PY" -m pip install transparent-background && ok "transparent-background installed" \
    || warn "transparent-background install failed - the 누끼 button will stay disabled."
fi

# ----------------------------------------------------------------- comfyui
if [ "$DO_COMFY" -eq 1 ]; then
  step "AI: ComfyUI (for Flux masked inpaint)"
  if [ ! -d "$COMFY_DIR/.git" ]; then
    info "cloning ComfyUI -> $COMFY_DIR"
    mkdir -p "$(dirname "$COMFY_DIR")"
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$COMFY_DIR" || { echo "git clone failed (is git installed?)"; exit 1; }
  else ok "ComfyUI already cloned at $COMFY_DIR"; fi

  if [ ! -x "$COMFY_DIR/venv/bin/python" ]; then
    info "creating ComfyUI venv"
    "$PY" -m venv "$COMFY_DIR/venv"
  fi
  info "installing ComfyUI requirements (this pulls torch - a few minutes)"
  "$COMFY_DIR/venv/bin/python" -m pip install --upgrade pip >/dev/null
  "$COMFY_DIR/venv/bin/python" -m pip install -r "$COMFY_DIR/requirements.txt" \
    && ok "ComfyUI environment ready" \
    || warn "ComfyUI requirements hit an error - see torch/CUDA notes in SETUP_AI.md"
fi

# ----------------------------------------------------------------- models
if [ "$DO_MODELS" -eq 1 ]; then
  step "AI: Flux Kontext models (~17 GB, ungated Comfy-Org build)"
  base="https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files"
  get_file "$base/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors" "$COMFY_DIR/models/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"
  get_file "$base/text_encoders/clip_l.safetensors"                          "$COMFY_DIR/models/text_encoders/clip_l.safetensors"
  get_file "$base/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors"         "$COMFY_DIR/models/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors"
  get_file "$base/vae/ae.safetensors"                                        "$COMFY_DIR/models/vae/ae.safetensors"
  ok "all Flux Kontext models in place"
fi

# ----------------------------------------------------------------- summary
step "Done"
printf "Run the atlas:\n"; info "$PY server.py            # -> http://127.0.0.1:8000/"
if [ "$DO_COMFY" -eq 1 ]; then
  printf "\nTo enable Flux '부분 수정' (masked inpaint), in a SEPARATE terminal start ComfyUI:\n"
  info "$COMFY_DIR/venv/bin/python $COMFY_DIR/main.py --listen 127.0.0.1 --port $COMFY_PORT"
  printf "then launch the atlas pointed at it:\n"
  info "COMFY_URL=http://127.0.0.1:$COMFY_PORT $PY server.py"
fi
printf "${c_gray}\nThe core tool works without any AI extras. See SETUP_AI.md for details.${c_off}\n"
