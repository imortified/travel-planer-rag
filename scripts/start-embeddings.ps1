param([int]$Port = 8082, [int]$GpuLayers = 0)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$server = Join-Path $projectRoot '.local\llama.cpp\b11840\llama-server.exe'
$model = Join-Path $projectRoot '.local\models\Qwen3-Embedding-0.6B-Q8_0.gguf'
if (!(Test-Path -LiteralPath $server) -or !(Test-Path -LiteralPath $model)) {
    throw 'Run scripts/setup-llama.ps1 and scripts/setup-embeddings.ps1 first'
}
& $server --model $model --alias 'Qwen3-Embedding-0.6B-Q8_0' --embedding --pooling last `
    --host 127.0.0.1 --port $Port --ctx-size 2048 --n-gpu-layers $GpuLayers `
    --parallel 1 --batch-size 2048 --ubatch-size 2048 --threads 8 `
    --cors-origins localhost --no-cors-credentials
if ($LASTEXITCODE -ne 0) { throw 'Embedding llama-server exited with an error' }
