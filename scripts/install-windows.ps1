param(
    [string]$TunnelClientPath,
    [string]$ApiKey,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$venvPath = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$toolsRoot = Join-Path $repoRoot '.tools'
$tunnelTargetDir = Join-Path $toolsRoot 'tunnel-client'
$tunnelTarget = Join-Path $tunnelTargetDir 'tunnel-client.exe'
$configDir = Join-Path $repoRoot 'config'
$configPath = Join-Path $configDir 'code-harness.toml'
$codeUpBat = Join-Path $repoRoot 'scripts\code-up.bat'
$codeDownBat = Join-Path $repoRoot 'scripts\code-down.bat'

function Write-Step([string]$Message) {
    Write-Host "[code-base] $Message" -ForegroundColor Cyan
}

function Resolve-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @('py', '-3.12')
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @('python')
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step 'Python nao encontrado. Instalando Python 3.12 via winget...'
        & winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw 'Falha ao instalar Python 3.12 via winget.'
        }

        $pythonCandidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312\python.exe')
        )
        foreach ($candidate in $pythonCandidates) {
            if (Test-Path -LiteralPath $candidate) {
                return @($candidate)
            }
        }
    }

    throw 'Python 3.12+ nao encontrado. Instale o Python e execute novamente.'
}

function Ensure-Ripgrep {
    if (Get-Command rg -ErrorAction SilentlyContinue) {
        return
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step 'Ripgrep nao encontrado. Instalando via winget...'
        & winget install --id BurntSushi.ripgrep.MSVC -e --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw 'Falha ao instalar ripgrep via winget.'
        }

        $wingetRg = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\rg.exe'
        if (Test-Path -LiteralPath $wingetRg) {
            $env:PATH = "$(Split-Path $wingetRg -Parent);$env:PATH"
            return
        }
    }

    throw "Ripgrep nao encontrado. Instale-o e confirme que 'rg --version' funciona."
}

function Resolve-TunnelClient {
    param([string]$ExplicitPath)

    if (Test-Path -LiteralPath $tunnelTarget) {
        return $tunnelTarget
    }

    $candidates = New-Object System.Collections.Generic.List[string]

    if ($ExplicitPath) {
        $candidates.Add($ExplicitPath)
    }

    $command = Get-Command tunnel-client.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        $candidates.Add($command.Source)
    }

    $toolsDir = Join-Path $env:USERPROFILE 'Tools'
    if (Test-Path -LiteralPath $toolsDir) {
        Get-ChildItem -Path $toolsDir -Filter tunnel-client.exe -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            New-Item -ItemType Directory -Path $tunnelTargetDir -Force | Out-Null
            Copy-Item -LiteralPath $candidate -Destination $tunnelTarget -Force
            return $tunnelTarget
        }
    }

    Write-Host ''
    Write-Host 'tunnel-client.exe nao foi encontrado automaticamente.' -ForegroundColor Yellow
    Write-Host 'Informe o caminho completo do executavel para copia-lo para .tools\tunnel-client.' -ForegroundColor Yellow
    $manualPath = Read-Host 'Caminho do tunnel-client.exe'
    if (-not $manualPath -or -not (Test-Path -LiteralPath $manualPath)) {
        throw 'tunnel-client.exe nao encontrado no caminho informado.'
    }

    New-Item -ItemType Directory -Path $tunnelTargetDir -Force | Out-Null
    Copy-Item -LiteralPath $manualPath -Destination $tunnelTarget -Force
    return $tunnelTarget
}

function Ensure-ApiKey {
    param([string]$ExplicitApiKey)

    $current = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'User')
    if ($current) {
        return
    }

    if ($ExplicitApiKey) {
        [Environment]::SetEnvironmentVariable('CONTROL_PLANE_API_KEY', $ExplicitApiKey, 'User')
        $env:CONTROL_PLANE_API_KEY = $ExplicitApiKey
        return
    }

    $secure = Read-Host 'CONTROL_PLANE_API_KEY' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if (-not $plain) {
            throw 'CONTROL_PLANE_API_KEY nao informada.'
        }
        [Environment]::SetEnvironmentVariable('CONTROL_PLANE_API_KEY', $plain, 'User')
        $env:CONTROL_PLANE_API_KEY = $plain
    }
    finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

function Ensure-ProjectConfig {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null

    if (Test-Path -LiteralPath $configPath) {
        return
    }

    $projectsRoot = Split-Path $repoRoot -Parent
    $repoLiteral = $repoRoot.Replace("'", "''")
    $rootLiteral = $projectsRoot.Replace("'", "''")

    $content = @"
default_project = "code-base"

allowed_project_roots = [
    '$rootLiteral'
]

[projects.code-base]
path = '$repoLiteral'
"@

    [System.IO.File]::WriteAllText(
        $configPath,
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Ensure-PowerShellProfile {
    $profilePath = $PROFILE.CurrentUserCurrentHost
    $profileDir = Split-Path $profilePath -Parent
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    if (-not (Test-Path -LiteralPath $profilePath)) {
        New-Item -ItemType File -Path $profilePath -Force | Out-Null
    }

    $startMarker = '# >>> code-base managed functions >>>'
    $endMarker = '# <<< code-base managed functions <<<'
    $batPath = $codeUpBat.Replace("'", "''")
    $downPath = $codeDownBat.Replace("'", "''")
    $block = @"
$startMarker
function code-up {
    cmd /c '$batPath'
}

function code-down {
    cmd /c '$downPath'
}
$endMarker
"@

    $current = Get-Content -LiteralPath $profilePath -Raw -ErrorAction SilentlyContinue
    if ($null -eq $current) { $current = '' }

    $pattern = [regex]::Escape($startMarker) + '.*?' + [regex]::Escape($endMarker)
    if ([regex]::IsMatch($current, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline)) {
        $updated = [regex]::Replace(
            $current,
            $pattern,
            $block.Trim(),
            [Text.RegularExpressions.RegexOptions]::Singleline
        )
    }
    else {
        $separator = if ($current -and -not $current.EndsWith("`n")) { "`r`n`r`n" } else { "`r`n" }
        $updated = $current + $separator + $block.Trim() + "`r`n"
    }

    [System.IO.File]::WriteAllText(
        $profilePath,
        $updated,
        [System.Text.UTF8Encoding]::new($false)
    )

    return $profilePath
}

Write-Step "Repositorio: $repoRoot"

$pythonCommand = Resolve-PythonCommand
Ensure-Ripgrep
$tunnel = Resolve-TunnelClient -ExplicitPath $TunnelClientPath
Ensure-ApiKey -ExplicitApiKey $ApiKey
Ensure-ProjectConfig

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Step 'Criando virtualenv .venv...'
    if ($pythonCommand.Count -eq 2) {
        & $pythonCommand[0] $pythonCommand[1] -m venv $venvPath
    }
    else {
        & $pythonCommand[0] -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Falha ao criar .venv.'
    }
}

Write-Step 'Instalando code-base no virtualenv...'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Falha ao atualizar pip.' }
& $venvPython -m pip install -e $repoRoot
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar code-base.' }

Write-Step 'Validando TOML...'
& $venvPython -c "import pathlib,tomllib; p=pathlib.Path(r'$configPath'); tomllib.loads(p.read_text(encoding='utf-8')); print('TOML OK:', p)"
if ($LASTEXITCODE -ne 0) { throw 'Configuracao TOML invalida.' }

$profilePath = Ensure-PowerShellProfile

Write-Host ''
Write-Host 'Instalacao concluida.' -ForegroundColor Green
Write-Host "Tunnel client: $tunnel"
Write-Host "Config:        $configPath"
Write-Host "PowerShell:    $profilePath"
Write-Host ''
Write-Host 'Nas proximas sessoes, basta executar: code-up' -ForegroundColor Green

if ($Start) {
    Write-Step 'Iniciando code-up...'
    & $codeUpBat
    exit $LASTEXITCODE
}
