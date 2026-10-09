param(
    [Parameter(Mandatory=$true)][string]$RunRoot,
    [int]$Port = 55449,
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..\..\..").Path
if ((git -C $root rev-parse HEAD) -ne '1c06ef9c2a78a556cd8c7ded89853c19ec9a856a') { throw 'BASELINE_MISMATCH' }
if ((git -C $root branch --show-current) -ne 'lab/data-integrity-evidence-storage') { throw 'WORKSPACE_OWNERSHIP_MISMATCH' }
if (Test-Path $RunRoot) { throw 'RunRoot must be new; never reuse a cluster' }
if ($Port -eq 5432 -or $Port -lt 1024 -or $Port -gt 65535) { throw 'Nonstandard port required' }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw 'Port already occupied' }
if (Get-ChildItem Env: | Where-Object { $_.Name.StartsWith('PG') -and $_.Value }) { throw 'PG environment overrides forbidden' }
New-Item -ItemType Directory -Path $RunRoot | Out-Null
$cluster = Join-Path $RunRoot 'cluster'
& "$PostgresBin\initdb.exe" -D $cluster -U evidence_lab -A trust --encoding=UTF8 --locale=C
if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
$control = & "$PostgresBin\pg_controldata.exe" -D $cluster
$identityLine = $control | Select-String '^(Database system identifier|Identificador de sistema):'
if (-not $identityLine) { throw 'Unrecognized pg_controldata locale; retain cluster for diagnosis' }
$identifier = ($identityLine.Line -split ':',2)[1].Trim()
if ($identifier -notmatch '^\d+$') { throw 'Cannot identify newly created cluster' }
@{ marker='OPENCODE_EVIDENCE_STORAGE_LAB_V1'; worktree=$root;
   baseline='1c06ef9c2a78a556cd8c7ded89853c19ec9a856a'; system_identifier=$identifier;
   port=$Port; postgres_bin=$PostgresBin; cluster=$cluster } | ConvertTo-Json | Set-Content (Join-Path $RunRoot 'owner.json') -Encoding UTF8
& "$PostgresBin\pg_ctl.exe" -D $cluster -l "$RunRoot\server.log" -o "-h 127.0.0.1 -p $Port -c fsync=on -c synchronous_commit=on -c full_page_writes=on" -w start
if ($LASTEXITCODE -ne 0) { throw 'pg_ctl failed; cluster is retained for diagnosis' }
foreach ($scale in @('gate','100k','1m')) {
    & "$PostgresBin\createdb.exe" -h 127.0.0.1 -p $Port -U evidence_lab "prediktia_lab_evidence_storage_$scale"
    if ($LASTEXITCODE -ne 0) { throw 'createdb failed' }
}
Write-Output "Owned disposable cluster: $cluster; port=$Port; system_identifier=$identifier"
