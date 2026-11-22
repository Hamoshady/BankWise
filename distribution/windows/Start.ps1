$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Invoke-Docker { & docker @args; if ($LASTEXITCODE -ne 0) { throw "Docker command failed. Check Docker Desktop is running with Linux containers." } }
Invoke-Docker info --format '{{.OSType}}'
if ((& docker info --format '{{.OSType}}') -ne 'linux') { throw 'Select Linux containers in Docker Desktop.' }
if (!(Test-Path '.env')) {
    function New-Secret {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
    }
    $lines = @('BANKWISE_UI_PORT=8501',('POSTGRES_PASSWORD='+(New-Secret).TrimEnd('=')),('BANKWISE_JWT_SECRET='+(New-Secret)),('BANKWISE_AUDIT_HMAC_KEY='+(New-Secret)),('BANKWISE_REVIEW_KEY='+(New-Secret)),('BANKWISE_INITIAL_ADMIN_PASSWORD='+(New-Secret)))
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'),$lines,[Text.UTF8Encoding]::new($false))
}
foreach ($tag in @('bankwise-share:api','bankwise-share:ui','postgres:16-alpine')) {
    & docker image inspect $tag *> $null
    if ($LASTEXITCODE -ne 0) { Invoke-Docker load --input images.tar; break }
}
# Setup is a single audit writer; stop API before re-running initialization.
Invoke-Docker compose stop ui api
Invoke-Docker compose up -d db
Invoke-Docker compose run --rm --no-deps setup
Invoke-Docker compose up -d --no-deps api
Write-Host 'Loading local models. First startup can take several minutes...'
$ready=$false
for ($i=0; $i -lt 120; $i++) {
    $id=(& docker compose ps -q api)
    if ($id -and ((& docker inspect --format '{{.State.Health.Status}}' $id) -eq 'healthy')) { $ready=$true;break }
    Start-Sleep -Seconds 5
}
if (!$ready) { Invoke-Docker compose logs --tail 30 api; throw 'API not ready. Check available RAM and model files.' }
Invoke-Docker compose up -d --no-deps ui
Write-Host 'Open http://127.0.0.1:8501 . Create your own customer account.'
Write-Host 'Reviewer username: identity_officer. Reviewer password is BANKWISE_INITIAL_ADMIN_PASSWORD in .env.'
Start-Process 'http://127.0.0.1:8501'
