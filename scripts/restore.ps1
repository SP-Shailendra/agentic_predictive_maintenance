[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$DatabaseName = 'maintenance',
    [string]$DatabaseUser = 'maintenance'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
    throw "Backup file not found: $BackupFile"
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
$containerBackup = "/tmp/fieldnote-restore-$([guid]::NewGuid().ToString('N')).dump"

if (-not $PSCmdlet.ShouldProcess("Empty PostgreSQL database '$DatabaseName'", "Restore archive from '$resolvedBackup'")) {
    return
}

$preRestore = $false
Push-Location $projectRoot
try {
    $tableCount = docker compose exec -T db psql -U $DatabaseUser -d $DatabaseName -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name NOT IN ('spatial_ref_sys', 'geometry_columns');"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect target database '$DatabaseName'." }
    if ([int]$tableCount -gt 0) { throw "Target database '$DatabaseName' is not empty. Restore to a newly created empty database; this script will not drop existing tables." }

    docker compose cp $resolvedBackup "db:$containerBackup" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Copying the backup into the container failed.' }
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U $DatabaseUser -d $DatabaseName -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare TimescaleDB for restore.' }
    $preRestore = $true
    docker compose exec -T db pg_restore --no-owner --no-privileges --single-transaction --exit-on-error -U $DatabaseUser -d $DatabaseName $containerBackup | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_restore failed; the transaction was rolled back.' }
    Write-Output "Restore completed for database '$DatabaseName'."
}
finally {
    if ($preRestore) {
        docker compose exec -T db psql -v ON_ERROR_STOP=1 -U $DatabaseUser -d $DatabaseName -c "SELECT timescaledb_post_restore();" | Out-Null
    }
    docker compose exec -T db rm -f $containerBackup | Out-Null
    Pop-Location
}