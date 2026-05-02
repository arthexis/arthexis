$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
Set-Location $repoRoot

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:NODE_OPTIONS = "--no-deprecation"
$env:ARTHEXIS_DB_BACKEND = "sqlite"

$gtkCandidates = @(
    "C:\Program Files\GTK3-Runtime Win64\bin",
    "C:\msys64\mingw64\bin"
)
foreach ($candidate in $gtkCandidates) {
    if (Test-Path $candidate) {
        $env:Path = "$candidate;$env:Path"
    }
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

& $python manage.py check --fail-level ERROR
if ($LASTEXITCODE -ne 0) {
    throw "Django system check failed with exit code $LASTEXITCODE"
}
