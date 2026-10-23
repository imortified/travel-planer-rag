$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& "$projectRoot\backend\.venv\Scripts\python.exe" "$PSScriptRoot\setup_llama.py"
if ($LASTEXITCODE -ne 0) { throw 'llama.cpp setup failed' }
