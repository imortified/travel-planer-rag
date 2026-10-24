param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [ValidateSet('greedy', 'beam')][string]$Algorithm = 'greedy',
    [ValidateRange(1, 128)][int]$BeamWidth = 32,
    [ValidateSet('Demo City', 'Lisbon', 'Porto', 'Sintra')][string]$Destination = 'Demo City',
    [string]$TravelDate,
    [string]$EndDate
)
$ErrorActionPreference = 'Stop'
if ($Destination -ne 'Demo City' -and -not $TravelDate) {
    throw 'For a real city pass -TravelDate YYYY-MM-DD'
}

function Send-Api([string]$Method, [string]$Path, $Payload) {
    $json = $Payload | ConvertTo-Json -Depth 10
    Invoke-RestMethod -Method $Method -Uri "$BaseUrl/api/v1$Path" -ContentType 'application/json' -Body $json
}

$alice = Send-Api 'Post' '/users' @{name='Alice'}
$bob = Send-Api 'Post' '/users' @{name='Bob'}
$group = Send-Api 'Post' '/groups' @{name='Demo friends'}
foreach ($person in @($alice, $bob)) {
    $null = Send-Api 'Post' "/groups/$($group.id)/members" @{user_id=$person.id}
}
$null = Send-Api 'Put' "/groups/$($group.id)/members/$($alice.id)/preferences" @{interests=@{history=1;food=0.5}}
$null = Send-Api 'Put' "/groups/$($group.id)/members/$($bob.id)/preferences" @{interests=@{nature=1;food=0.8}}
$tripPayload = @{budget=10000;destination=$Destination}
if ($Destination -eq 'Demo City') { $tripPayload.mandatory = @('museum') }
if ($TravelDate) { $tripPayload.travel_date = $TravelDate }
if ($EndDate) { $tripPayload.end_date = $EndDate }
$trip = Send-Api 'Post' "/groups/$($group.id)/trips" $tripPayload
$route = Send-Api 'Post' "/trips/$($trip.id)/routes?algorithm=$Algorithm&beam_width=$BeamWidth" @{}
Invoke-RestMethod -Uri "$BaseUrl/api/v1/routes/$($route.id)" | ConvertTo-Json -Depth 10
