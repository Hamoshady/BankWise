$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
Write-Host 'Optional: downloads historical Financial data directly from CTU; requires internet. Review upstream terms first: https://relational.fel.cvut.cz/dataset/Financial'
$answer=Read-Host 'Download for local research? Type YES'
if ($answer -ne 'YES') { exit }
$customer=Read-Host 'Existing customer username to grant research access (not the reviewer)'
if ($customer -notmatch '^[a-z0-9][a-z0-9_.-]{2,63}$') { throw 'Invalid username' }
New-Item -ItemType Directory -Force berka | Out-Null
& docker compose stop ui api
if ($LASTEXITCODE -ne 0) { throw 'Stop failed' }
& docker compose run --rm --no-deps --user root -v "${PSScriptRoot}/source/scripts:/tools:ro" -v "${PSScriptRoot}/berka:/data" setup sh -c 'python -m pip install pymysql && python /tools/download_berka.py --output /data'
if ($LASTEXITCODE -ne 0) { throw 'Download failed. Run Start.bat to restore services.' }
& docker compose run --rm --no-deps -v "${PSScriptRoot}/berka:/data:ro" setup python -m bankwise.database.import_financial /data --grant "${customer}:1"
if ($LASTEXITCODE -ne 0) { throw 'Import failed. Run Start.bat to restore services.' }
& .\Start.ps1
