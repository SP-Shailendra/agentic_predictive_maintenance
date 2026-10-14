[CmdletBinding()]
param(
    [string]$DatabaseName = 'maintenance',
    [string]$DatabaseUser = 'maintenance'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$verificationDatabase = "fieldnote_restore_$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$temporaryDirectory = Join-Path $env:TEMP $verificationDatabase
$backupScript = Join-Path $PSScriptRoot 'backup.ps1'
$restoreScript = Join-Path $PSScriptRoot 'restore.ps1'
$createdDatabase = $false

function Get-RecordCounts([string]$Name) {
    $query = "SELECT 'plants', count(*) FROM plants UNION ALL SELECT 'assets', count(*) FROM assets UNION ALL SELECT 'sensor_data', count(*) FROM sensor_data UNION ALL SELECT 'work_orders', count(*) FROM work_orders UNION ALL SELECT 'workflow_events', count(*) FROM workflow_events UNION ALL SELECT 'cmms_outbox', count(*) FROM cmms_outbox UNION ALL SELECT 'knowledge_chunks', count(*) FROM knowledge_chunks ORDER BY 1;"
    $result = docker compose exec -T db psql -U $DatabaseUser -d $Name -Atc $query
    if ($LASTEXITCODE -ne 0) { throw "Could not read verification counts from '$Name'." }
    return ($result -join "`n").Trim()
}

Push-Location $projectRoot
try {
    $sourceCounts = Get-RecordCounts $DatabaseName
    $backupFile = & $backupScript -OutputDirectory $temporaryDirectory -DatabaseName $DatabaseName -DatabaseUser $DatabaseUser
    if (-not $backupFile) { throw 'Backup script did not return a dump path.' }
    docker compose exec -T db createdb -U $DatabaseUser $verificationDatabase | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the temporary verification database.' }
    $createdDatabase = $true
    & $restoreScript -BackupFile $backupFile -DatabaseName $verificationDatabase -DatabaseUser $DatabaseUser -Confirm:$false | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Restore script failed during verification.' }
    $restoredCounts = Get-RecordCounts $verificationDatabase
    if ($sourceCounts -cne $restoredCounts) {
        throw "Restored table counts differ. Source: $sourceCounts; restored: $restoredCounts"
    }
    Write-Output 'Backup and restore verification passed.'
    Write-Output $restoredCounts
}
finally {
    if ($createdDatabase) {
        docker compose exec -T db dropdb --if-exists --force -U $DatabaseUser $verificationDatabase | Out-Null
    }
    if (Test-Path -LiteralPath $temporaryDirectory) {
        Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
    }
    Pop-Location
}