param(
    [int]$Port = 8083,
    [int]$GpuLayers = 0,
    [int]$ContextSize = 8192
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$server = Join-Path $projectRoot '.local\llama.cpp\b11840\llama-server.exe'
$model = Join-Path $projectRoot '.local\models\Qwen3-8B-Q4_K_M.gguf'
if (!(Test-Path -LiteralPath $server) -or !(Test-Path -LiteralPath $model)) {
    throw 'Run backend\.venv\Scripts\python.exe scripts\setup_llama.py --manifest data/judge-runtime.json first'
}
& $server --model $model --alias 'Qwen3-8B-Q4_K_M' --host 127.0.0.1 --port $Port `
    --ctx-size $ContextSize --n-gpu-layers $GpuLayers --parallel 1 --batch-size 128 `
    --ubatch-size 128 --threads 8 --jinja --cors-origins localhost --no-cors-credentials
if ($LASTEXITCODE -ne 0) { throw 'RAGAS judge server exited with an error' }
