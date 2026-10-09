@echo off
setlocal EnableExtensions

call "%~dp0_code_env.bat"
if errorlevel 1 exit /b 1

set "PATH=%TUNNEL_HOME%;%PATH%"
set "PROFILE=my-agent"
set "TUNNEL_ID=tunnel_6a95b9bc19a481919347ae0cde561305"
set "TUNNEL_HEALTH_URL_FILE=%TEMP%\tunnel-client-%PROFILE%-health.url"

if exist "%TUNNEL_HEALTH_URL_FILE%" del /q "%TUNNEL_HEALTH_URL_FILE%"

set "MCP_CMD=%MCP_PYTHON% -m code_harness mcp serve"

for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY','User')"`) do set "CONTROL_PLANE_API_KEY=%%i"

if not defined CONTROL_PLANE_API_KEY (
    echo ERROR: CONTROL_PLANE_API_KEY ausente.
    exit /b 1
)

if not exist "%MCP_PYTHON%" (
    echo ERROR: virtualenv nao encontrado:
    echo   %MCP_PYTHON%
    exit /b 1
)

if not exist "%TUNNEL_HOME%\tunnel-client.exe" (
    echo ERROR: tunnel-client nao encontrado:
    echo   %TUNNEL_HOME%\tunnel-client.exe
    exit /b 1
)

echo [code-base] instalando pacote...
cd /d "%REPO%" || exit /b 1
call ".venv\Scripts\activate.bat" || exit /b 1
python -m pip install -e . -q || exit /b 1

echo [code-base] parando tunnel anterior...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'tunnel-client.exe'\" | Where-Object { $_.CommandLine -match '--profile my-agent(\s|$)' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo [code-base] recriando profile...
"%TUNNEL_HOME%\tunnel-client.exe" init ^
    --sample sample_mcp_stdio_local ^
    --profile "%PROFILE%" ^
    --force ^
    --tunnel-id "%TUNNEL_ID%" ^
    --mcp-command "%MCP_CMD%" ^
    --health-listen-addr "127.0.0.1:0"

if errorlevel 1 exit /b 1

echo [code-base] validando...
"%TUNNEL_HOME%\tunnel-client.exe" doctor --profile "%PROFILE%"
if errorlevel 1 exit /b 1

echo.
echo ================================================
echo code-base MCP
echo ================================================
echo repo:   %REPO%
echo config: %CODE_HARNESS_PROJECT_CONFIG%
echo review: http://127.0.0.1:%CODE_HARNESS_REVIEW_PORT%
echo ================================================
echo.

"%TUNNEL_HOME%\tunnel-client.exe" run ^
    --profile "%PROFILE%" ^
    --health.url-file "%TUNNEL_HEALTH_URL_FILE%"

exit /b %ERRORLEVEL%
