param(
    [switch]$Semantic,
    [switch]$Parsers,
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
$venvHarness = Join-Path $venvPath "Scripts\code-harness.exe"
& $venvPython -m pip install --upgrade pip

$extras = [System.Collections.Generic.List[string]]::new()
$extras.Add("dev")
if ($Semantic) { $extras.Add("semantic") }
if ($Parsers) { $extras.Add("parsers") }
$extraList = [string]::Join(",", $extras)
$packageSpec = "${repoRoot}[$extraList]"
$constraints = Join-Path $repoRoot "constraints\semantic.txt"

if ($Semantic) {
    & $venvPython -m pip install -c $constraints -e $packageSpec
    $env:CODE_HARNESS_SEMANTIC = "1"
    & $venvHarness --project $repoRoot models prepare
    & $venvHarness --project $repoRoot doctor --deep
} else {
    & $venvPython -m pip install -e $packageSpec
    & $venvHarness --project $repoRoot doctor
}

Write-Output "Environment ready: $venvPath"
