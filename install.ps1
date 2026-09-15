# Arthur Loop experimental Windows installer.
# Public main does not host this file — raw .../main/install.ps1 is a 404.
# From a checkout of this branch:  ./install.ps1
# Env: ARTHUR_LOOP_REPO, ARTHUR_LOOP_REF, ARTHUR_LOOP_HOME, ARTHUR_LOOP_BIN
$ErrorActionPreference = "Stop"

$Repo = if ($env:ARTHUR_LOOP_REPO) { $env:ARTHUR_LOOP_REPO } else { "https://github.com/myrrazor/arthur-loop.git" }
$Ref = $env:ARTHUR_LOOP_REF
$Dest = if ($env:ARTHUR_LOOP_HOME) { $env:ARTHUR_LOOP_HOME } else { Join-Path $HOME ".arthur-loop" }
$BinDir = if ($env:ARTHUR_LOOP_BIN) { $env:ARTHUR_LOOP_BIN } else { Join-Path $HOME ".local\bin" }

function Fail([string]$Message) {
    Write-Error "error: $Message"
    exit 1
}

$python = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue }
if (-not $python) { Fail "python3 is required (3.9 or newer)" }

& $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
if ($LASTEXITCODE -ne 0) { Fail "python3 >= 3.9 is required" }

$here = Get-Location
if ((Test-Path (Join-Path $here "pyproject.toml")) -and (Select-String -Path (Join-Path $here "pyproject.toml") -Pattern 'name = "arthur-loop"' -Quiet)) {
    $Src = $here.Path
    Write-Host "Installing Arthur Loop from this checkout: $Src"
} else {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { Fail "git is required to fetch the repo" }
    $srcDir = Join-Path $Dest "src"
    if (Test-Path (Join-Path $srcDir ".git")) {
        Write-Host "Updating existing checkout at $srcDir"
        git -C $srcDir pull --ff-only --quiet
    } else {
        New-Item -ItemType Directory -Force -Path $Dest | Out-Null
        Write-Host "Cloning $Repo -> $srcDir"
        if ($Ref) {
            git clone --depth 1 --branch $Ref --quiet $Repo $srcDir
        } else {
            git clone --depth 1 --quiet $Repo $srcDir
        }
    }
    $Src = $srcDir
}

$Venv = Join-Path $Dest "venv"
Write-Host "Creating venv at $Venv"
& $python.Source -m venv --clear $Venv
$venvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = Join-Path $Venv "bin/python" }
& $venvPython -m pip install --quiet --upgrade pip | Out-Null
Write-Host "Installing arthur-loop (this pulls one dependency: rich)..."
& $venvPython -m pip install --quiet $Src
$arthur = Join-Path $Venv "Scripts\arthur.exe"
if (-not (Test-Path $arthur)) { $arthur = Join-Path $Venv "bin/arthur" }
if (-not (Test-Path $arthur)) { Fail "install finished but arthur is missing from the venv" }

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$shim = Join-Path $BinDir "arthur.cmd"
Set-Content -Path $shim -Value "@echo off`r`n`"$arthur`" %*" -Encoding ascii
Write-Host ""
Write-Host "Arthur Loop installed (experimental Windows): $shim"
Write-Host "Confirm with: arthur --version   (prints arthur 0.1.0 until the next tag)"
Write-Host "This installer does not claim MCP or arthur follow unless you installed this branch."
