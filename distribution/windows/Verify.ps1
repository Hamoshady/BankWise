$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$items=Get-Content '.\SHA256SUMS.json' -Raw | ConvertFrom-Json
foreach ($item in $items) {
    $actual=(Get-FileHash -LiteralPath $item.path -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $item.sha256) { throw ('Checksum mismatch: '+$item.path) }
}
Write-Host 'All package files verified.'
