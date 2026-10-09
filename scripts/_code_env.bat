@echo off

for %%I in ("%~dp0..") do set "REPO=%%~fI"

if not defined CODE_HARNESS_PROJECT_CONFIG set "CODE_HARNESS_PROJECT_CONFIG=%REPO%\config\code-harness.toml"
if not defined CODE_HARNESS_REVIEW_PORT set "CODE_HARNESS_REVIEW_PORT=8765"

set "CODE_HARNESS_RG_DEFAULT=%REPO%\.tools\ripgrep\rg.exe"
if exist "%CODE_HARNESS_RG_DEFAULT%" set "CODE_HARNESS_RG=%CODE_HARNESS_RG_DEFAULT%"

if "%CODE_HARNESS_RG%"=="" (
    for /f "delims=" %%i in ('where rg 2^>nul') do (
        set "CODE_HARNESS_RG=%%i"
        goto rg_resolved
    )
)

:rg_resolved

set "TUNNEL_HOME=%REPO%\.tools\tunnel-client"
set "MCP_PYTHON=%REPO:\=/%/.venv/Scripts/python.exe"

if not exist "%REPO%" (
    echo ERROR: repositorio code-base nao encontrado:
    echo   %REPO%
    exit /b 1
)

if not exist "%CODE_HARNESS_PROJECT_CONFIG%" (
    echo ERROR: configuracao nao encontrada:
    echo   %CODE_HARNESS_PROJECT_CONFIG%
    exit /b 1
)

exit /b 0
