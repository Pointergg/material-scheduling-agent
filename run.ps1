param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AgentArgs)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $runtime = $null
    $localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $localPython) {
        $runtime = $localPython
    } else {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch 'WindowsApps') {
            $runtime = $command.Source
        } elseif (Test-Path -LiteralPath $bundledPython) {
            $runtime = $bundledPython
        }
    }
    if (-not $runtime) { throw 'Python 3.11+ is required. Install Python and enable PATH.' }
    & $runtime -X utf8 -m src @AgentArgs
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
