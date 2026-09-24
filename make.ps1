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
        'gen-types'      = 'Regenerate frontend API types from the running backend'
        'build'          = 'Build the production dashboard bundle'
        'up'             = 'Start the containerised stack'
        'down'           = 'Stop the containerised stack'
        'logs'           = 'Tail container logs'
        'ps'             = 'Show container status'
        'clean'          = 'Remove build output, caches and the dev database'
        'wiki'           = 'Publish wiki/ to the GitHub wiki (no-op when unchanged)'
        'wiki-check'     = 'Report whether the GitHub wiki is behind wiki/, push nothing'
        'hooks'          = 'Install the git hooks, including post-commit wiki publishing'
        'hooks-uninstall' = 'Remove the git hooks this repo installed'
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

    'gen-types' { Invoke-Step $Frontend 'npm' @('run', 'gen:types') }

    'build' { Invoke-Step $Frontend 'npm' @('run', 'build') }

    'up' {
        Initialize-EnvFile
        Invoke-Step $RepoRoot 'docker' @('compose', 'up', '--build')
    }

    'down' { Invoke-Step $RepoRoot 'docker' @('compose', 'down') }

    'logs' { Invoke-Step $RepoRoot 'docker' @('compose', 'logs', '-f') }

    'ps' { Invoke-Step $RepoRoot 'docker' @('compose', 'ps') }

    'wiki' { Invoke-Step $RepoRoot 'python' @('scripts/publish_wiki.py') }

    'wiki-check' { Invoke-Step $RepoRoot 'python' @('scripts/publish_wiki.py', '--check') }

    'hooks' { Invoke-Step $RepoRoot 'python' @('scripts/install_hooks.py') }

    'hooks-uninstall' { Invoke-Step $RepoRoot 'python' @('scripts/install_hooks.py', '--uninstall') }

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
