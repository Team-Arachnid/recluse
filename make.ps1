<#
.SYNOPSIS
    Windows stand-in for GNU make.

.DESCRIPTION
    GNU make is not installed on Windows by default. This mirrors every target
    in the Makefile so the documented commands work out of the box:

        ./make.ps1 dev
        ./make.ps1 test
        ./make.ps1 up

    If you would rather use real make, install it and use the Makefile:
        winget install ezwinports.make
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',

    # Passed through to targets that take an argument, e.g.
    #   ./make.ps1 revision -m "add drift table"
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$Backend = Join-Path $RepoRoot 'backend'
$Frontend = Join-Path $RepoRoot 'frontend'

function Invoke-Step {
    param(
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$Command,
        [string[]]$Arguments = @()
    )

    Push-Location $WorkingDirectory
    try {
        & $Command @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Command $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Initialize-EnvFile {
    $envFile = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $RepoRoot '.env.example') $envFile
        Write-Host 'created .env' -ForegroundColor Yellow
    }
}

function Show-Help {
    Write-Host ''
    Write-Host '  Recluse targets' -ForegroundColor Cyan
    Write-Host ''
    $targets = [ordered]@{
        'env'            = 'Create .env from .env.example if absent'
        'install'        = 'Install backend and frontend dependencies'
        'dev'            = 'Run backend and frontend together (native, hot reload)'
        'backend'        = 'Run the API only'
        'frontend'       = 'Run the dashboard only'
        'migrate'        = 'Apply database migrations'
        'revision'       = 'Autogenerate a migration: ./make.ps1 revision -m "msg"'
        'test'           = 'Run every test'
        'test-backend'   = 'Run the backend test suite'
        'test-frontend'  = 'Run the dashboard test suite'
        'lint'           = 'Lint backend and typecheck frontend'
        'format'         = 'Format the backend'
        'typecheck'      = 'Typecheck the frontend'
        'openapi'        = 'Phase 8: rewrite the API contract snapshot and regenerate the types'
        'gen-types'      = 'Regenerate frontend API types from the running backend'
        'build'          = 'Build the production dashboard bundle'
        'up'             = 'Start the containerised stack'
        'down'           = 'Stop the containerised stack'
        'logs'           = 'Tail container logs'
        'ps'             = 'Show container status'
        'clean'          = 'Remove build output, caches and the dev database'
        'docs'           = 'Build the documentation site into docs/_site (needs Ruby + bundler)'
        'docs-serve'     = 'Preview the documentation site at http://localhost:4000/recluse/'
        'data-fetch'     = 'Phase 1: download CICIDS2017 into data/raw (Kaggle mirror)'
        'data'           = 'Phase 1: clean, split and fit the preprocessing bundle'
        'data-clean'     = 'Phase 1: clean data/raw CSVs into data/interim Parquet'
        'data-split'     = 'Phase 1: temporally split data/interim into data/processed'
        'data-fit'       = 'Phase 1: fit the preprocessing bundle from the train split'
        'train'          = 'Phase 2: train the RandomForest baseline and report it'
        'train-rf'       = 'Phase 2: RandomForest baseline, tuned on the validation day'
        'train-lgbm'     = 'Phase 2: LightGBM upgrade; promoted only if it wins, then re-evaluated'
        'evaluate'       = 'Phase 2: score the test day into reports/phase2_supervised.md'
        'ablation-port'  = 'Phase 2: raw vs bucketed destination port'
        'train-anomaly'  = 'Phase 3: benign-only autoencoder and tau_anom'
        'ablation-input' = 'Phase 3: pick the Stage 2 input clip bound'
        'loao'           = 'Phase 4: leave-one-attack-out, into reports/loao.md'
        'drift-reference' = 'Phase 7: cut the PSI reference from the training split'
        'drift'          = 'Phase 7: compute one PSI snapshot over the sampled window'
        'retrain'        = 'Phase 7: fit a challenger from analyst labels and gate it'
        'models'         = 'Phase 8: install the committed model release'
        'seed'           = 'Phase 8: fill an empty database with a real, replayed demo'
        'release'        = 'Phase 8 (maintainers): rebuild backend/release'
        'calibrate'      = 'Phase 9: local tau_anom from the shadow burn-in'
    }
    foreach ($key in $targets.Keys) {
        Write-Host ('    {0,-15} {1}' -f $key, $targets[$key])
    }
    Write-Host ''
}

switch ($Target) {
    'help' { Show-Help }

    'env' { Initialize-EnvFile }

    'install' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('sync')
        Invoke-Step $Frontend 'npm' @('install', '--no-fund')
    }

    'dev' {
        Initialize-EnvFile
        Invoke-Step $RepoRoot 'python' @('scripts/dev.py')
    }

    'backend' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'uvicorn', 'app.main:app', '--reload')
    }

    'frontend' { Invoke-Step $Frontend 'npm' @('run', 'dev') }

    'migrate' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'alembic', 'upgrade', 'head')
    }

    'revision' {
        $message = ($Rest | Where-Object { $_ -ne '-m' }) -join ' '
        if (-not $message) { throw 'Usage: ./make.ps1 revision -m "message"' }
        Invoke-Step $Backend 'uv' @('run', 'alembic', 'revision', '--autogenerate', '-m', $message)
    }

    'test' {
        Invoke-Step $Backend 'uv' @('run', 'pytest')
        Invoke-Step $Frontend 'npm' @('run', 'test')
    }

    'test-backend' { Invoke-Step $Backend 'uv' @('run', 'pytest') }

    'test-frontend' { Invoke-Step $Frontend 'npm' @('run', 'test') }

    'lint' {
        Invoke-Step $Backend 'uv' @('run', 'ruff', 'check', '.')
        Invoke-Step $Frontend 'npm' @('run', 'typecheck')
    }

    'format' {
        Invoke-Step $Backend 'uv' @('run', 'ruff', 'format', '.')
        Invoke-Step $Backend 'uv' @('run', 'ruff', 'check', '--fix', '.')
    }

    'typecheck' { Invoke-Step $Frontend 'npm' @('run', 'typecheck') }

    # The API contract: rewrite the committed OpenAPI snapshot from the code,
    # then regenerate the dashboard's types from it.
    'openapi' {
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'app.contract')
        Invoke-Step $Frontend 'npm' @('run', 'gen:types', '--', '--from', '../backend/tests/snapshots/openapi.json')
    }

    'gen-types' { Invoke-Step $Frontend 'npm' @('run', 'gen:types') }

    'build' { Invoke-Step $Frontend 'npm' @('run', 'build') }

    'up' {
        Initialize-EnvFile
        Invoke-Step $RepoRoot 'docker' @('compose', 'up', '--build')
    }

    'down' { Invoke-Step $RepoRoot 'docker' @('compose', 'down') }

    'logs' { Invoke-Step $RepoRoot 'docker' @('compose', 'logs', '-f') }

    'ps' { Invoke-Step $RepoRoot 'docker' @('compose', 'ps') }

    'data-fetch' { Invoke-Step $Backend 'uv' @('run', 'python', '../scripts/fetch_data.py') }

    'data' { Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.preprocess', '--all') }

    'data-clean' { Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.clean') }

    'data-split' { Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.split') }

    'data-fit' { Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.preprocess') }

    'train' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_supervised', '--algorithm', 'rf')
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.evaluate')
    }

    'train-rf' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_supervised', '--algorithm', 'rf')
    }

    # Re-evaluates after training: a promotion rewrites the model card, and
    # without a fresh evaluation the API would serve LightGBM beside
    # RandomForest's numbers.
    'train-lgbm' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_supervised', '--algorithm', 'lgbm')
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.evaluate')
    }

    'evaluate' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.evaluate')
    }

    'ablation-port' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_supervised', '--port-ablation')
    }

    'train-anomaly' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_autoencoder')
    }

    'ablation-input' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.train_autoencoder', '--input-ablation')
    }

    'loao' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.loao')
    }

    'drift-reference' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.drift_reference')
    }

    # Nightly in a real deployment. A job rather than a thread inside the API: a
    # full scan of the sample table at 3am should not compete with the alert
    # stream for the same event loop.
    'drift' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.drift_job')
    }

    'retrain' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'training.retrain', '--now')
    }

    'models' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'app.release', 'install')
    }

    # Replays the committed demo flows through the real pipeline into an empty
    # database, then runs the drift job. `./make.ps1 seed --reset` starts over.
    'seed' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'alembic', 'upgrade', 'head')
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'app.release', 'install')
        $seedArgs = @('run', 'python', '-m', 'app.seed')
        if ($Rest) { $seedArgs += $Rest }
        Invoke-Step $Backend 'uv' $seedArgs
    }

    'calibrate' {
        Initialize-EnvFile
        $calibrateArgs = @('run', 'python', '-m', 'training.calibrate_live')
        if ($Rest) { $calibrateArgs += $Rest }
        Invoke-Step $Backend 'uv' $calibrateArgs
    }

    'release' {
        Initialize-EnvFile
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'app.seed', 'sample')
        Invoke-Step $Backend 'uv' @('run', 'python', '-m', 'app.release', 'build')
    }

    'docs' { Invoke-Step (Join-Path $RepoRoot 'docs') 'bundle' @('exec', 'jekyll', 'build') }

    'docs-serve' { Invoke-Step (Join-Path $RepoRoot 'docs') 'bundle' @('exec', 'jekyll', 'serve', '--livereload') }

    'clean' {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
            (Join-Path $Frontend 'dist'),
            (Join-Path $Frontend 'node_modules/.vite'),
            (Join-Path $Backend '.pytest_cache'),
            (Join-Path $Backend '.ruff_cache')
        Get-ChildItem -Path $RepoRoot -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path (Join-Path $RepoRoot 'data') -Filter 'ids.db*' -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Host 'cleaned' -ForegroundColor Yellow
    }

    default {
        Write-Host "unknown target: $Target" -ForegroundColor Red
        Show-Help
        exit 1
    }
}
