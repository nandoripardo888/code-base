$ErrorActionPreference = 'Stop'

$request = [Console]::In.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    [string]$request.script,
    [ref]$tokens,
    [ref]$parseErrors
)

$commands = @(
    $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true) |
        ForEach-Object { $_.GetCommandName() } |
        Where-Object { $_ }
)
$textFragments = @(
    $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $node -is [System.Management.Automation.Language.ExpandableStringExpressionAst] -or
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true) | ForEach-Object { $_.Extent.Text }
)
$hasVariable = @(
    $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)
).Count -gt 0
$features = [System.Collections.Generic.List[string]]::new()
$commandNames = @($commands | ForEach-Object { $_.ToLowerInvariant() })

if ($commandNames | Where-Object { $_ -in @('invoke-expression', 'iex') }) { $features.Add('invoke_expression') }
if ($commandNames -contains 'start-process') { $features.Add('start_process') }
if ($commandNames -contains 'add-type') { $features.Add('add_type') }
if ($commandNames | Where-Object { $_ -in @('invoke-webrequest', 'invoke-restmethod', 'wget', 'curl') }) { $features.Add('download_cradle') }
if ($commandNames | Where-Object { $_ -in @('get-service', 'stop-service', 'start-service', 'set-service') }) { $features.Add('service_control') }
if ($commandNames -contains 'remove-item') { $features.Add('remove_item') }
if ($commandNames -contains 'import-module' -and $hasVariable) { $features.Add('import_module_dynamic') }

$commandText = [string]::Join("`n", $textFragments)
if ($commandText -match '(?i)(^|\s)-(enc|encodedcommand)(\s|$)') { $features.Add('encoded_command') }
if ($commandText -match '(?i)(HKLM:|HKCU:|Registry::)') { $features.Add('registry') }
if ($commandText -match '(?m)^\s*&\s*\$') { $features.Add('call_operator_dynamic') }
if ($commandText -match '(?m)^\s*\.\s*\$') { $features.Add('dot_source_dynamic') }

[pscustomobject]@{
    major_version = $PSVersionTable.PSVersion.Major
    commands = @($commands)
    text_fragments = @($textFragments)
    dynamic_features = @($features | Select-Object -Unique)
    parse_errors = @($parseErrors | ForEach-Object { $_.Message })
} | ConvertTo-Json -Compress -Depth 4
