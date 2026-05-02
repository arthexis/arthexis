$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
Set-Location $repoRoot

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:NODE_OPTIONS = "--no-deprecation"
$env:ARTHEXIS_DB_BACKEND = "sqlite"

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
        $env:Path = "$candidate;$env:Path"
    }
}
if ($gtkDllDirs.Count -eq 0) {
    throw "No GTK runtime bin directory found for WeasyPrint."
}
$env:WEASYPRINT_DLL_DIRECTORIES = $gtkDllDirs -join ";"

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
