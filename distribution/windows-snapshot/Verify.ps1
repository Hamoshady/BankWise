$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$manifest = Get-Content 'manifest.json' -Raw | ConvertFrom-Json
foreach ($entry in $manifest) {
    $file=Join-Path $PSScriptRoot $entry.path
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing file: $($entry.path)" }
    $hash=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.sha256) { throw "Checksum mismatch: $($entry.path)" }
}
Write-Host 'All package files verified.'
