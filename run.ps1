[CmdletBinding()]
param(
    [switch]$WithoutModels,
    [switch]$CheckOnly,
    [ValidateRange(30, 900)][int]$StartupTimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectRoot = $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
$next = Join-Path $frontendRoot 'node_modules\next\dist\bin\next'
$ownedProcesses = [System.Collections.Generic.List[object]]::new()
$logRoot = $null
$locationPushed = $false

function Test-LocalPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync('127.0.0.1', $Port)
        if (!$pending.Wait(500)) { return $false }
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Test-HttpReady([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Test-ExistingModel([int]$Port, [string]$Model) {
    if (!(Test-LocalPort $Port)) { return $false }
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 2
        if (@($response.data.id) -contains $Model) {
            Write-Host "Reusing model $Model on port $Port."
            return $true
        }
    } catch { }
    throw "Port $Port is occupied, but the expected model $Model is not ready. Stop that process and retry."
}

function Test-ExistingApp([int]$Port) {
    if (!(Test-LocalPort $Port)) { return $false }
    try {
        if ($Port -eq 8001) {
            $schema = Invoke-RestMethod 'http://127.0.0.1:8001/openapi.json' -TimeoutSec 3
            if ($schema.info.title -eq 'Group Travel Planner' -and (Test-HttpReady 'http://127.0.0.1:8001/health')) {
                Write-Host 'Reusing backend on port 8001.'
                return $true
            }
        } else {
            $page = Invoke-WebRequest 'http://127.0.0.1:3001/' -UseBasicParsing -TimeoutSec 30
            # Match the application title without relying on PowerShell 5.1 source encoding.
            if ($page.Content -match '<title>\u041f\u043e\u043f\u0443\u0442\u043d\u043e \u2014 \u043f\u043b\u0430\u043d \u043f\u0443\u0442\u0435\u0448\u0435\u0441\u0442\u0432\u0438\u044f</title>') {
                Write-Host 'Reusing frontend on port 3001.'
                return $true
            }
        }
    } catch { }
    throw "Port $Port is occupied by an unrecognized or unready application. Stop that process and retry."
}

function Start-ServiceProcess([string]$Name, [string]$File, [string[]]$Arguments, [string]$Directory) {
    $stdout = Join-Path $logRoot "$Name.stdout.log"
    $stderr = Join-Path $logRoot "$Name.stderr.log"
    # Paths are passed as separate, quoted Windows arguments, never as shell commands.
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Embedded quotes are not supported in process arguments.' }
        '"' + $argument + '"'
    }
    $process = Start-Process -FilePath $File -ArgumentList $quoted -WorkingDirectory $Directory `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    # Retain the handle so Windows PowerShell can read the exit code later.
    $null = $process.Handle
    $entry = [pscustomobject]@{ Name = $Name; Process = $process; Stdout = $stdout; Stderr = $stderr }
    $ownedProcesses.Add($entry)
    Write-Host "Started $Name (PID $($process.Id))."
    return $entry
}

function Assert-ProcessesAlive {
    foreach ($entry in $ownedProcesses) {
        if ($entry.Process.HasExited) {
            throw "$($entry.Name) exited (code $($entry.Process.ExitCode)). See $($entry.Stderr) and $($entry.Stdout)."
        }
    }
}

function Wait-Service([string]$Name, [string]$Url) {
    Write-Host "Waiting for $Name..."
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        Assert-ProcessesAlive
        if (Test-HttpReady $Url) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name did not become ready within $StartupTimeoutSeconds seconds. Logs: $logRoot"
}

try {
    # Check prerequisites before starting any application processes.
    foreach ($required in @($python, $next, (Join-Path $projectRoot '.env'))) {
        if (!(Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Missing $required. Complete the initial setup in README.md first."
        }
    }
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    $docker = (Get-Command docker.exe -ErrorAction Stop).Source
    $powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $reuseBackend = Test-ExistingApp 8001
    $reuseFrontend = Test-ExistingApp 3001
    $reuseLlm = $false
    $reuseEmbeddings = $false
    if (!$WithoutModels) {
        $reuseLlm = Test-ExistingModel 8081 'Qwen3-4B-Q4_K_M'
        $reuseEmbeddings = Test-ExistingModel 8082 'Qwen3-Embedding-0.6B-Q8_0'
        $modelFiles = @()
        if (!$reuseLlm) { $modelFiles += '.local\models\Qwen3-4B-Q4_K_M.gguf' }
        if (!$reuseEmbeddings) { $modelFiles += '.local\models\Qwen3-Embedding-0.6B-Q8_0.gguf' }
        if ($modelFiles.Count -gt 0) { $modelFiles += '.local\llama.cpp\b11840\llama-server.exe' }
        foreach ($relative in $modelFiles) {
            if (!(Test-Path -LiteralPath (Join-Path $projectRoot $relative) -PathType Leaf)) {
                throw "Missing $relative. Run scripts/setup-llama.ps1 and scripts/setup-embeddings.ps1 first, or use -WithoutModels."
            }
        }
    }
    Push-Location -LiteralPath $projectRoot
    $locationPushed = $true
    & $docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker is not available. Start Docker Desktop (Linux containers), then retry.' }
    & $docker compose version --short
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose v2 is required.' }
    if ($CheckOnly) {
        Write-Host 'Prerequisites OK. Nothing started; no migrations applied.' -ForegroundColor Green
        return
    }

    $logRoot = Join-Path $projectRoot ('.local\logs\run-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    Write-Host "Logs: $logRoot"
    Write-Host 'Starting PostgreSQL, Redis and Qdrant...'
    & $docker compose up -d --no-recreate --wait --wait-timeout 120 postgres redis qdrant
    if ($LASTEXITCODE -ne 0) { throw 'Docker services failed to start. Check docker compose ps / logs.' }

    if (!$reuseBackend) {
        Write-Host 'Applying database migrations...'
        Push-Location -LiteralPath $backendRoot
        try {
            & $python -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw 'Database migration failed; application startup cancelled.' }
        } finally { Pop-Location }
    } else {
        Write-Host 'Migrations skipped for the running backend. After updating code, stop the old backend and rerun.'
    }

    if (!$WithoutModels) {
        if (!$reuseLlm) {
            $null = Start-ServiceProcess 'llm' $powerShell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $projectRoot 'scripts\start-llama.ps1')) $projectRoot
        }
        if (!$reuseEmbeddings) {
            $null = Start-ServiceProcess 'embeddings' $powerShell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $projectRoot 'scripts\start-embeddings.ps1')) $projectRoot
        }
        Wait-Service 'LLM' 'http://127.0.0.1:8081/health'
        Wait-Service 'embeddings' 'http://127.0.0.1:8082/health'
    }

    if (!$reuseBackend) {
        $null = Start-ServiceProcess 'backend' $python @('-m', 'app.run', '--reload', '--port', '8001') $backendRoot
    }
    Wait-Service 'backend' 'http://127.0.0.1:8001/health'
    if (!$reuseFrontend) {
        $null = Start-ServiceProcess 'frontend' $node @($next, 'dev', '--hostname', '127.0.0.1', '--port', '3001') $frontendRoot
    }
    Wait-Service 'frontend' 'http://127.0.0.1:3001/'

    Write-Host ''
    Write-Host 'Ready: http://127.0.0.1:3001' -ForegroundColor Green
    Write-Host 'API docs: http://127.0.0.1:8001/docs'
    Write-Host 'Health:   http://127.0.0.1:8001/health'
    Write-Host 'Ctrl+C stops processes started by this launcher. Docker services and reused processes remain running.'
    while ($true) {
        Assert-ProcessesAlive
        Start-Sleep -Seconds 1
    }
} finally {
    # Only process trees created by this invocation belong to the launcher.
    for ($index = $ownedProcesses.Count - 1; $index -ge 0; $index--) {
        $entry = $ownedProcesses[$index]
        if (!$entry.Process.HasExited) {
            Write-Host "Stopping $($entry.Name)..."
            & "$env:SystemRoot\System32\taskkill.exe" /PID $entry.Process.Id /T /F 2>&1 | Out-Null
        }
        $entry.Process.Dispose()
    }
    if ($locationPushed) { Pop-Location }
}
