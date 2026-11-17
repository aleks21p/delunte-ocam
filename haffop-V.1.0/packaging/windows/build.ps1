# Build the Windows 11 edition: dist\Haffop.exe (single file, asks for administrator rights)
#   usage (PowerShell):  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
$ErrorActionPreference = 'Stop'
$Src = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $Src

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ is required: winget install Python.Python.3.12'
}

python -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller nvidia-ml-py

# Render the SVG logo to a multi-size .ico with Qt (no extra dependencies)
& .\.venv-build\Scripts\python.exe packaging\windows\make_icon.py haffop\resources\haffop.svg build\haffop.ico
& .\.venv-build\Scripts\python.exe packaging\windows\make_version.py build\version.txt

& .\.venv-build\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --noupx `
    --name Haffop `
    --uac-admin `
    --icon build\haffop.ico `
    --version-file build\version.txt `
    --paths . `
    --add-data "haffop\resources;resources" `
    --collect-submodules haffop `
    packaging\windows\run_haffop.py

Write-Host "Built $Src\dist\Haffop.exe"
