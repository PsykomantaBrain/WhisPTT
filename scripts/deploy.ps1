<#
.SYNOPSIS
  Build WhisPTT on this PC and deploy it to a Steam Deck over SSH/scp.

.DESCRIPTION
  Workflow: build (pnpm) -> stage the runtime files -> scp to the Deck's /tmp
  -> sudo-install into ~/homebrew/plugins -> restart Decky.

  No rsync needed (Windows ships scp). Run from anywhere:
      .\scripts\deploy.ps1 -Deck deck@192.168.1.42
  Or set $env:WHISPTT_DECK once and just run .\scripts\deploy.ps1

  You'll be prompted for the Deck password (login + sudo). Set up SSH key auth
  to skip the login prompts (see README).

.PARAMETER Deck
  user@host for the Deck, e.g. deck@192.168.1.42

.PARAMETER PluginName
  Folder name under ~/homebrew/plugins (default: WhisPTT)

.PARAMETER SkipBuild
  Deploy the existing dist/ without rebuilding.
#>
param(
  [string]$Deck = $env:WHISPTT_DECK,
  [string]$PluginName = "WhisPTT",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

if (-not $Deck) {
  throw "No Deck target. Pass -Deck deck@<ip> or set `$env:WHISPTT_DECK."
}

# Repo root = parent of this script's folder.
$root = Split-Path $PSScriptRoot -Parent

# --- 1. build ---
if (-not $SkipBuild) {
  $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue).Source
  if (-not $pnpm) { $pnpm = Join-Path (npm config get prefix) 'pnpm.cmd' }
  Write-Host "==> building..." -ForegroundColor Cyan
  & $pnpm -C $root run build
  if ($LASTEXITCODE -ne 0) { throw "build failed" }
}
if (-not (Test-Path (Join-Path $root "dist\index.js"))) {
  throw "dist\index.js missing - build first (don't use -SkipBuild)."
}

# --- 2. stage exactly the runtime files ---
$stageParent = Join-Path $env:TEMP "whisptt-deploy"
$stage = Join-Path $stageParent $PluginName
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -Recurse (Join-Path $root "dist") $stage
Copy-Item (Join-Path $root "plugin.json")  $stage
Copy-Item (Join-Path $root "package.json") $stage
Copy-Item (Join-Path $root "*.py")         $stage   # main.py + backend modules
Write-Host "==> staged: $((Get-ChildItem $stage -Recurse -File).Count) files" -ForegroundColor Cyan

# --- 3. push to the Deck's /tmp ---
# scp on Windows reads the first ':' as a host separator, so a path like
# "C:\..." breaks. Avoid it by cd-ing into the staging parent and using a
# RELATIVE path (no drive-letter colon).
$remoteTmp = "/tmp/$PluginName"
Write-Host "==> copying to ${Deck}:$remoteTmp" -ForegroundColor Cyan
ssh $Deck "rm -rf '$remoteTmp'"
if ($LASTEXITCODE -ne 0) { throw "ssh (clean /tmp) failed" }
Push-Location $stageParent
try {
  scp -r $PluginName "${Deck}:$remoteTmp"
  if ($LASTEXITCODE -ne 0) { throw "scp failed" }
} finally { Pop-Location }

# --- 4. install + restart Decky (needs sudo -> tty) ---
$dest = "/home/deck/homebrew/plugins/$PluginName"
$cmd = @(
  "sudo rm -rf '$dest'",
  "sudo mv '$remoteTmp' '$dest'",
  "sudo chown -R root:root '$dest'",
  "sudo chmod -R a+rX '$dest'",      # root-owned but world-readable (match stock plugins; Dolphin can browse)
  "sudo systemctl restart plugin_loader"
) -join " && "
Write-Host "==> installing on Deck + restarting Decky (enter sudo password)..." -ForegroundColor Cyan
ssh -t $Deck $cmd
if ($LASTEXITCODE -ne 0) { throw "remote install failed" }

Write-Host "==> done. Open the Quick Access Menu -> WhisPTT." -ForegroundColor Green
