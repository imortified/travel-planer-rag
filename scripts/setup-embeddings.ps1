$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& "$projectRoot\backend\.venv\Scripts\python.exe" "$PSScriptRoot\setup_llama.py" --manifest data/embedding-runtime.json
if ($LASTEXITCODE -ne 0) { throw 'Embedding model setup failed' }
