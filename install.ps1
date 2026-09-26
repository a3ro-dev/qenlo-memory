# qenlo-memory: irm https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$src = "qenlo-memory @ https://github.com/a3ro-dev/qenlo-memory/archive/refs/heads/main.zip"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "  installing uv, the python tool manager qenlo-memory ships through"
    irm https://astral.sh/uv/install.ps1 | iex *> $null
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (Get-Command qenlo-memory -ErrorAction SilentlyContinue) { qenlo-memory stop *> $null }
uv tool install --force --quiet $src
if ($LASTEXITCODE) { throw "uv tool install failed. if an agent is running qenlo-memory, close it and run this again." }
& (Join-Path (uv tool dir --bin) "qenlo-memory.exe") install
