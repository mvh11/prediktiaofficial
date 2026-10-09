param(
    [Parameter(Mandatory=$true)][string]$RunRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^(gate[0-9]*|100k[0-9]*|1m[0-9]*)$')][string]$Database,
    [Parameter(Mandatory=$true)][ValidateSet('init','gate','bench')][string]$Command,
    [string]$ReportName,
    [string]$GateReportName = 'gate-gate.json',
    [int]$Samples = 15
)
$ErrorActionPreference = 'Stop'
$backend = (Resolve-Path "$PSScriptRoot\..\..").Path
$owner = Get-Content "$RunRoot\owner.json" -Raw | ConvertFrom-Json
$db = "prediktia_lab_evidence_storage_$Database"
$env:EVIDENCE_STORAGE_DATABASE_URL = "postgresql+psycopg://evidence_lab@127.0.0.1:$($owner.port)/$db"
$env:EVIDENCE_STORAGE_ALLOW_DESTRUCTIVE = "127.0.0.1:$($owner.port)/$db"
$env:EVIDENCE_STORAGE_CLUSTER = $owner.cluster
$env:EVIDENCE_STORAGE_SYSTEM_IDENTIFIER = $owner.system_identifier
if (-not $ReportName) { $ReportName = "$Database-$Command.json" }
$output = Join-Path $RunRoot $ReportName
$arguments = @('-B','-m','tools.evidence_storage_lab',$Command,'--output',$output)
if ($Command -eq 'bench') {
    if ($Database.StartsWith('gate')) { throw 'Bench requires a staged scale database' }
    $observations = if ($Database.StartsWith('100k')) { '100000' } else { '1000000' }
    $arguments += @('--observations',$observations,'--samples',"$Samples",'--gate-report',"$RunRoot\$GateReportName")
}
Push-Location $backend
try {
    & "$backend\.venv\Scripts\python.exe" @arguments
    if ($LASTEXITCODE -ne 0) { throw "Lab command failed ($LASTEXITCODE); evidence retained" }
} finally { Pop-Location }
