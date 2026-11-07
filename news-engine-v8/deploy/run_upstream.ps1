$ErrorActionPreference = 'Stop'

try {
    $projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    Set-Location -LiteralPath $projectPath
    $env:UV_CACHE_DIR = Join-Path $projectPath '.uv-cache'

    & uv run --no-sync python -m news_engine.pipeline --output outputs/latest-news.json
    $collectExitCode = $LASTEXITCODE
    if ($collectExitCode -notin @(0, 2)) {
        exit 1
    }

    if ($collectExitCode -eq 2) {
        exit 2
    }
    exit 0
}
catch {
    Write-Error 'Unable to execute article workflows.' -ErrorAction Continue
    exit 1
}
