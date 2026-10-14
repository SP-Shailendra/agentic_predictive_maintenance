[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\.backups'),
    [string]$DatabaseName = 'maintenance',
    [string]$DatabaseUser = 'maintenance'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
}
$containerBackup = "/tmp/fieldnote-$([guid]::NewGuid().ToString('N')).dump"
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$destination = Join-Path $outputPath "fieldnote-$timestamp.dump"

New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
Push-Location $projectRoot
try {
    docker compose exec -T db pg_dump -U $DatabaseUser -d $DatabaseName -Fc -f $containerBackup | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed.' }
    docker compose cp "db:$containerBackup" $destination | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Copying the database dump out of the container failed.' }
    Write-Output $destination
}
finally {
    docker compose exec -T db rm -f $containerBackup | Out-Null
    Pop-Location
}