$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Invoke-Docker { & docker @args; if ($LASTEXITCODE -ne 0) { throw 'Docker command failed. See the output above.' } }
if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop, enable WSL 2, then start Docker Desktop before running Start.bat.' }
Invoke-Docker info --format '{{.OSType}}'
if ((& docker info --format '{{.OSType}}') -ne 'linux') { throw 'Select Linux containers in Docker Desktop.' }
$architecture = (& docker info --format '{{.Architecture}}')
if ($architecture -notin @('x86_64','amd64')) { throw 'This bundle requires an Intel/AMD x86-64 computer.' }
$installed = @(& docker image ls --format '{{.Repository}}:{{.Tag}}')
$expected = Get-Content 'images.json' -Raw | ConvertFrom-Json
$load = $false
foreach ($image in $expected) {
    if ($installed -notcontains $image.tag) { $load=$true;break }
    $actual=(& docker image inspect --format '{{.Id}}' $image.tag)
    if ($actual -ne $image.id) { $load=$true;break }
}
if ($load) { Invoke-Docker load --input images.tar.gz }
Invoke-Docker compose up -d
Write-Host 'Restoring the database and loading models. First startup can take several minutes.'
$ready=$false
for ($i=0; $i -lt 180; $i++) {
    $id=(& docker compose ps -q api)
    if ($id -and ((& docker inspect --format '{{.State.Health.Status}}' $id) -eq 'healthy')) { $ready=$true;break }
    Start-Sleep -Seconds 5
}
if (!$ready) { Invoke-Docker compose logs --tail 30 initialize api; throw 'Not ready. Check RAM and Docker logs. Do not delete database volumes.' }
$port='8501'
Get-Content '.env' | ForEach-Object { if ($_ -match '^BANKWISE_UI_PORT=(\d+)$') { $port=$Matches[1] } }
Start-Process "http://127.0.0.1:$port"
Write-Host 'Use the same existing login credentials as the original installation.'
