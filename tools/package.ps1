# Builds the mod end to end and zips it for Nexus: dist\ts3_characters-<version>.zip, with a ts3_characters\ folder
# to drop into the game's mods\ folder. Needs this PC's game installs, Blender, and the DIM2 tool folders.
#   .\tools\package.ps1 -Version 1.0.0
param(
    [Parameter(Mandatory)] [string]$Version,
    [string]$Blender = $(if ($env:BLENDER) { $env:BLENDER } else { "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" })
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Step([string]$name, [scriptblock]$cmd) {
    Write-Host "== $name"
    # Windows PowerShell turns any stderr line of a native tool (Blender warnings) into an error under "Stop"
    $ErrorActionPreference = "Continue"
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$name failed (exit $LASTEXITCODE)" }
}

# The tag names the release, mod.json names the mod: they must agree (versions are only bumped on purpose)
$modVersion = (Get-Content mod.json -Raw | ConvertFrom-Json).version
if ($modVersion -ne $Version) { throw "mod.json version is $modVersion but the release is ${Version}: bump mod.json first" }
if (-not (Test-Path $Blender)) { throw "Blender not found at $Blender (set BLENDER)" }

# Meshes first, then the Blender scene they feed, then the renders the second build packs into the UI archives
Invoke-Step "build (meshes)" { python build_ts3_characters.py }
Invoke-Step "blender scene data" { python tools\export_blender_data.py }
Invoke-Step "blender scene" { & $Blender -b --python tools\blender_scene.py }
Invoke-Step "figure renders" { & $Blender -b build\blender_scene\ts3_characters.blend --python tools\render_icons.py }
Invoke-Step "portrait renders" { & $Blender -b build\blender_scene\ts3_characters.blend --python tools\render_portraits.py }
Invoke-Step "build (with icons)" { python build_ts3_characters.py }

foreach ($f in "mod.json", "ts3_characters.lua", "ui\icons\icons.zip", "ui\virtualreadercharacters\virtualreadercharacters.zip") {
    if (-not (Test-Path (Join-Path "build\mod" $f))) { throw "build\mod\$f is missing" }
}

$stage = Join-Path $Root "build\package"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $stage "ts3_characters") | Out-Null
Copy-Item -Recurse "build\mod\*" (Join-Path $stage "ts3_characters")
New-Item -ItemType Directory -Force dist | Out-Null
$zip = Join-Path $Root "dist\ts3_characters-$Version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "ts3_characters") -DestinationPath $zip -CompressionLevel Optimal
Write-Host "== packaged $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
