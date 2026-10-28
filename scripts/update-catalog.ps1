param(
    [ValidateSet('Lisbon','Porto','Sintra')][string[]]$Cities = @('Lisbon','Porto','Sintra'),
    [switch]$Download,
    [switch]$CheckPrices,
    [switch]$Index,
    [string]$TravelDate = (Get-Date -Format 'yyyy-MM-dd')
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$folder = Join-Path $projectRoot '.local\downloads'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$pbf = Join-Path $folder 'portugal-latest.osm.pbf'
$extract = Join-Path $projectRoot '.local\portugal-with-food.json'
Push-Location $projectRoot
try {
    if ($Download -or !(Test-Path -LiteralPath $pbf)) {
        & $python backend/download_portugal.py
        if ($LASTEXITCODE -ne 0) { throw 'Portugal download failed' }
    }
    & $python backend/extract_portugal.py --input $pbf --output $extract
    if ($LASTEXITCODE -ne 0) { throw 'Portugal extraction failed' }
    foreach ($city in $Cities) {
        & $python backend/refresh_catalog.py --city $city --extract $extract
        if ($LASTEXITCODE -ne 0) { throw "Catalog publication failed: $city" }
        if ($CheckPrices) {
            & $python backend/check_menu_sources.py --city $city --output ".local/menu-source-checks-$city.json"
            if ($LASTEXITCODE -ne 0) { throw "Source review failed: $city" }
        }
        if ($Index) {
            & $python backend/index_catalog.py --city $city --travel-date $TravelDate
            if ($LASTEXITCODE -ne 0) { throw "Semantic indexing failed: $city" }
        }
    }
} finally { Pop-Location }
