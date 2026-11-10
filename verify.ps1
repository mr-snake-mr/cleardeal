# One command that answers "is it real".
#
# It runs the things a claim about this repo rests on, in the order that fails
# fastest: the skill gate, lint, migrations from empty, then the suite. Anything
# non-zero stops the run, because a partial verification reported as success is
# worse than no verification.
#
# `Continue`, not `Stop`: alembic writes its INFO log to stderr, and PowerShell
# 5.1 raises a NativeCommandError for that whenever the output is captured or
# redirected — so `.\verify.ps1 > log.txt` died inside a successful migration.
# Strictness comes from the explicit $LASTEXITCODE checks below, which nothing
# can skip.

$ErrorActionPreference = "Continue"
$backend = Join-Path $PSScriptRoot "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "No virtualenv at $python. Create one with:" -ForegroundColor Yellow
    Write-Host "  cd backend; python -m venv .venv; .\.venv\Scripts\pip install -e `".[dev]`""
    exit 1
}

Push-Location $backend
try {
    # First, because it is the cheapest check and the only one that can stop a
    # commit for a reason no test would ever catch: the work was not declared.
    Write-Host "== skill gate ==" -ForegroundColor Cyan
    & $python (Join-Path $PSScriptRoot "tools\skillgate.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "== ruff ==" -ForegroundColor Cyan
    & $python -m ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "== migrations: upgrade -> downgrade -> upgrade ==" -ForegroundColor Cyan
    $env:DATABASE_URL = "sqlite:///./verify.db"
    $env:SESSION_SECRET = "verify-secret-2f8c1d4e6a9b0c3f5e7d1a2b4c6d8e0f"
    $env:CREDENTIALS_ENCRYPTION_KEY = "6f1c2b7d4e8a90c3f5d6b7a1e2c4d8f0a3b5c7d9e1f2a4b6c8d0e2f4a6b8c0d2"
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m alembic -c alembic.ini downgrade base
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "== tests ==" -ForegroundColor Cyan
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "VERIFIED" -ForegroundColor Green
}
finally {
    Remove-Item (Join-Path $backend "verify.db") -ErrorAction SilentlyContinue
    Pop-Location
}
