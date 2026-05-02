param(
    [string] $Mode = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
Set-Location $repoRoot

if (-not $env:PYTHONDONTWRITEBYTECODE) { $env:PYTHONDONTWRITEBYTECODE = "1" }
if (-not $env:NODE_OPTIONS) { $env:NODE_OPTIONS = "--no-deprecation" }
if (-not $env:ARTHEXIS_DB_BACKEND) { $env:ARTHEXIS_DB_BACKEND = "sqlite" }
if ($env:pythonLocation) {
    $env:Path = "$env:pythonLocation;$env:pythonLocation\Scripts;$env:Path"
}

$gtkCandidates = @()
if ($env:WEASYPRINT_DLL_DIRECTORIES) {
    $gtkCandidates += $env:WEASYPRINT_DLL_DIRECTORIES -split ";" | Where-Object { $_ }
}
$gtkCandidates += @(
    "C:\Program Files\GTK3-Runtime Win64\bin",
    "C:\msys64\mingw64\bin"
)
$gtkCandidates = $gtkCandidates | Select-Object -Unique
$gtkDllDirs = @()
foreach ($candidate in $gtkCandidates) {
    if (Test-Path $candidate) {
        $gtkDllDirs += $candidate
    }
}
if ($gtkDllDirs.Count -eq 0) {
    throw "No GTK runtime bin directory found for WeasyPrint."
}
$env:WEASYPRINT_DLL_DIRECTORIES = $gtkDllDirs -join ";"

if ($Mode -eq "--cold") {
    Remove-Item -LiteralPath ".venv" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ".locks\requirements.bundle.sha256" -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ".locks\requirements.hashes" -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ".locks\requirements.install-ts" -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath ".locks\pip.version" -Force -ErrorAction SilentlyContinue
    Remove-Item -Path "db*.sqlite3" -Force -ErrorAction SilentlyContinue
} elseif ($Mode) {
    throw "Unknown option: $Mode"
}

$bootstrapPython = (Get-Command python -ErrorAction Stop).Source
& $bootstrapPython scripts\sort_pyproject_deps.py --check
if ($LASTEXITCODE -ne 0) {
    throw "pyproject dependency ordering check failed with exit code $LASTEXITCODE"
}

& $bootstrapPython scripts\generate_requirements.py --check
if ($LASTEXITCODE -ne 0) {
    throw "generated requirements check failed with exit code $LASTEXITCODE"
}

cmd.exe /c install.bat
if ($LASTEXITCODE -ne 0) {
    throw "install.bat failed with exit code $LASTEXITCODE"
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Expected virtual environment Python at $python"
}

& $python scripts\check_editable_install_import.py
if ($LASTEXITCODE -ne 0) {
    throw "Editable install import check failed with exit code $LASTEXITCODE"
}

& $python scripts\check_migration_conflicts.py
if ($LASTEXITCODE -ne 0) {
    throw "Migration conflict check failed with exit code $LASTEXITCODE"
}

& $python manage.py migrations check
if ($LASTEXITCODE -ne 0) {
    throw "Django migrations check failed with exit code $LASTEXITCODE"
}

& $python manage.py migrate --noinput --database default
if ($LASTEXITCODE -ne 0) {
    throw "Django migrate failed with exit code $LASTEXITCODE"
}

& $python manage.py check --fail-level ERROR
if ($LASTEXITCODE -ne 0) {
    throw "Django system check failed with exit code $LASTEXITCODE"
}
