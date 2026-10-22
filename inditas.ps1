param([switch]$Paused)
$ErrorActionPreference = 'Stop'
$miniPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $miniPython)) {
    throw 'Előbb hozd létre a README szerinti saját Python-környezetet.'
}
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Paused) { & $miniPython -X utf8 -m mini_yard --paused }
    else { & $miniPython -X utf8 -m mini_yard }
} finally {
    Pop-Location
}
