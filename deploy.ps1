param(
    [string]$GameDir = "D:\SteamLibrary\steamapps\common\Disney Infinity 3.0 Gold Edition"
)

$ErrorActionPreference = "Stop"
$ModRoot = $PSScriptRoot
$BuildOut = Join-Path $ModRoot "build\mod"
$Target = Join-Path $GameDir "mods\ts3_characters"

if (-not (Test-Path $BuildOut)) {
    Write-Host "[TS3] Build output not found. Running build_ts3_characters.py..."
    python (Join-Path $ModRoot "build_ts3_characters.py")
}

if (Test-Path $Target) {
    Remove-Item -Recurse -Force $Target
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null

Copy-Item -Recurse -Force (Join-Path $BuildOut "*") $Target
Write-Host "[TS3] Deployed Toy Story 3 Characters mod to: $Target"
