param(
    [string]$Venv = ".venv"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot $Venv

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher not found. Install official Python 3.12+ from python.org."
}
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
    throw "Ripgrep not found. Install it and confirm that 'rg --version' succeeds."
}
if (-not (Test-Path -LiteralPath $venvPath)) {
    & py -3.12 -m venv $venvPath
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "$repoRoot[dev]"

Write-Output "Environment ready: $venvPath"
Write-Output "Try: code-harness grep hello --project $repoRoot"
