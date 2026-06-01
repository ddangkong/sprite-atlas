<#
.SYNOPSIS
  Sprite Atlas setup / bootstrap (Windows, PowerShell).

.DESCRIPTION
  Installs the core tool and, optionally, the AI extras:
    * core            FastAPI + Uvicorn + Pillow            (always)
    * -Matte          InSPyReNet background removal (누끼)   (pip: transparent-background)
    * -Comfy          clones ComfyUI into vendor\ + venv     (for Flux masked inpaint)
    * -Models         downloads the 4 Flux Kontext models    (~17 GB, into the ComfyUI clone)
    * -All            = -Matte -Comfy -Models

  ComfyUI and the Flux models are NOT bundled with this repo (multi-GB, separately
  licensed). This script fetches them for you and wires COMFY_URL so the atlas can
  find a locally-running ComfyUI. Without them the atlas still runs fully — the AI
  panels just stay disabled.

.EXAMPLE
  .\setup.ps1                 # core only
  .\setup.ps1 -Matte          # core + one-click background removal
  .\setup.ps1 -All            # core + matte + ComfyUI + Flux models (full AI)
  .\setup.ps1 -Comfy -Models  # set up ComfyUI and pull models, skip matte
#>
[CmdletBinding()]
param(
    [switch]$Matte,
    [switch]$Comfy,
    [switch]$Models,
    [switch]$All,
    [string]$ComfyDir = "$PSScriptRoot\vendor\ComfyUI",
    [int]$ComfyPort = 8188
)

$ErrorActionPreference = "Stop"
if ($All) { $Matte = $true; $Comfy = $true; $Models = $true }
# Models require the ComfyUI clone (that's where they live).
if ($Models) { $Comfy = $true }

function Info($m)  { Write-Host "  $m" -ForegroundColor Gray }
function Step($m)  { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok($m)    { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  [!] $m" -ForegroundColor Yellow }

function Resolve-Python {
    foreach ($c in @("python", "py")) {
        $exe = Get-Command $c -ErrorAction SilentlyContinue
        if ($exe) {
            try {
                $v = & $c -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
                if ($LASTEXITCODE -eq 0 -and $v) { return @{ Cmd = $c; Ver = $v } }
            } catch { }
        }
    }
    throw "Python 3 not found on PATH. Install it from https://www.python.org/ and re-run."
}

# Download $Url to $Dest. Skips if a non-trivial file already exists. Prefers
# curl.exe (resume + progress); falls back to Invoke-WebRequest.
function Get-File($Url, $Dest) {
    $dir = Split-Path -Parent $Dest
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $name = Split-Path -Leaf $Dest
    if (Test-Path $Dest) {
        $mb = [math]::Round((Get-Item $Dest).Length / 1MB, 1)
        if ($mb -gt 1) { Ok "$name already present (${mb} MB) - skipping"; return }
    }
    Info "downloading $name ..."
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -L --fail --retry 3 -C - -o $Dest $Url
        if ($LASTEXITCODE -ne 0) { throw "download failed ($name). If 401/403, run 'huggingface-cli login'." }
    } else {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
        } catch {
            throw "download failed ($name): $($_.Exception.Message). If 401/403, the model may be gated - run 'huggingface-cli login'."
        }
    }
    $mb = [math]::Round((Get-Item $Dest).Length / 1MB, 1)
    Ok "$name (${mb} MB)"
}

Write-Host "Sprite Atlas setup" -ForegroundColor White
$py = Resolve-Python
Info "python: $($py.Cmd) ($($py.Ver))   repo: $PSScriptRoot"
Info ("extras: matte={0} comfy={1} models={2}" -f $Matte, $Comfy, $Models)

# ---------------------------------------------------------------- core
Step "Core dependencies"
& $py.Cmd -m pip install -r "$PSScriptRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "core pip install failed" }
Ok "fastapi + uvicorn + pillow installed"

# ---------------------------------------------------------------- matte (누끼)
if ($Matte) {
    Step "AI: background removal (InSPyReNet / 누끼)"
    & $py.Cmd -m pip install transparent-background
    if ($LASTEXITCODE -ne 0) { Warn "transparent-background install failed - the 누끼 button will stay disabled." }
    else { Ok "transparent-background installed" }
}

# ---------------------------------------------------------------- ComfyUI
if ($Comfy) {
    Step "AI: ComfyUI (for Flux masked inpaint)"
    if (-not (Test-Path "$ComfyDir\.git")) {
        Info "cloning ComfyUI -> $ComfyDir"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ComfyDir) | Out-Null
        & git clone --depth 1 https://github.com/comfyanonymous/ComfyUI $ComfyDir
        if ($LASTEXITCODE -ne 0) { throw "git clone of ComfyUI failed (is git installed?)" }
    } else { Ok "ComfyUI already cloned at $ComfyDir" }

    $comfyVenvPy = "$ComfyDir\venv\Scripts\python.exe"
    if (-not (Test-Path $comfyVenvPy)) {
        Info "creating ComfyUI venv"
        & $py.Cmd -m venv "$ComfyDir\venv"
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    }
    Info "installing ComfyUI requirements (this pulls torch - a few minutes)"
    & $comfyVenvPy -m pip install --upgrade pip | Out-Null
    & $comfyVenvPy -m pip install -r "$ComfyDir\requirements.txt"
    if ($LASTEXITCODE -ne 0) { Warn "ComfyUI requirements install hit an error - check torch/CUDA notes in SETUP_AI.md" }
    else { Ok "ComfyUI environment ready" }
    Warn "GPU users: the default torch is CPU-only (Flux will be slow). For CUDA:"
    Info "  $comfyVenvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124"
}

# ---------------------------------------------------------------- models
if ($Models) {
    Step "AI: Flux Kontext models (~17 GB, ungated Comfy-Org build)"
    $base = "https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files"
    $modelList = @(
        @{ U = "$base/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors"; D = "$ComfyDir\models\diffusion_models\flux1-dev-kontext_fp8_scaled.safetensors" },
        @{ U = "$base/text_encoders/clip_l.safetensors";                          D = "$ComfyDir\models\text_encoders\clip_l.safetensors" },
        @{ U = "$base/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors";         D = "$ComfyDir\models\text_encoders\t5xxl_fp8_e4m3fn_scaled.safetensors" },
        @{ U = "$base/vae/ae.safetensors";                                        D = "$ComfyDir\models\vae\ae.safetensors" }
    )
    foreach ($m in $modelList) { Get-File $m.U $m.D }
    Ok "all Flux Kontext models in place"
}

# ---------------------------------------------------------------- summary
Step "Done"
Write-Host "Run the atlas:" -ForegroundColor White
Info "$($py.Cmd) server.py            # -> http://127.0.0.1:8000/"
if ($Comfy) {
    Write-Host "`nTo enable Flux '부분 수정' (masked inpaint), in a SEPARATE terminal start ComfyUI:" -ForegroundColor White
    Info "$ComfyDir\venv\Scripts\python.exe $ComfyDir\main.py --listen 127.0.0.1 --port $ComfyPort"
    Write-Host "then launch the atlas pointed at it:" -ForegroundColor White
    Info "`$env:COMFY_URL = 'http://127.0.0.1:$ComfyPort'; $($py.Cmd) server.py"
}
Write-Host "`nThe core tool works without any of the AI extras. See SETUP_AI.md for details." -ForegroundColor Gray
