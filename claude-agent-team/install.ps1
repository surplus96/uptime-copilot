<#
.SYNOPSIS
    Install the Claude Code agent team into a user or project .claude/agents directory.

.DESCRIPTION
    Copies every .md in this bundle's agents/ directory to the target.
    Existing files are never overwritten unless -Force is given; each skipped
    file is named in the output so nothing is lost silently.

.PARAMETER Scope
    user    -> $HOME\.claude\agents   (available in every project on this machine)
    project -> <Path>\.claude\agents  (committed alongside one repository)

.PARAMETER Path
    The project root. Required for -Scope project, ignored for -Scope user.

.PARAMETER Force
    Overwrite files that already exist at the target.

.PARAMETER DryRun
    Report what would happen and write nothing.

.EXAMPLE
    .\install.ps1 -Scope user

.EXAMPLE
    .\install.ps1 -Scope project -Path D:\cty_ai\some-project -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('user', 'project')]
    [string]$Scope = 'user',

    [string]$Path,

    [switch]$Force,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'agents'
if (-not (Test-Path -LiteralPath $source)) {
    throw "No agents directory beside this script. Expected: $source"
}

if ($Scope -eq 'project') {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "-Scope project needs -Path <project root>."
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Not a directory: $Path"
    }
    $root = (Resolve-Path -LiteralPath $Path).Path
} else {
    $root = $HOME
}

$target = Join-Path (Join-Path $root '.claude') 'agents'

Write-Host ""
Write-Host "  source : $source"
Write-Host "  target : $target"
Write-Host "  scope  : $Scope$(if ($DryRun) { '   (dry run -- nothing will be written)' })"
Write-Host ""

if (-not (Test-Path -LiteralPath $target)) {
    if ($DryRun) {
        Write-Host "  would create $target"
    } else {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        Write-Host "  created $target"
    }
}

$files = @(Get-ChildItem -LiteralPath $source -Filter *.md -File | Sort-Object Name)
if ($files.Count -eq 0) { throw "No .md files in $source" }

$installed = @()
$skipped   = @()
$replaced  = @()

foreach ($file in $files) {
    $destination = Join-Path $target $file.Name
    $exists = Test-Path -LiteralPath $destination

    if ($exists -and -not $Force) {
        $skipped += $file.Name
        continue
    }

    if (-not $DryRun) {
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }

    if ($exists) { $replaced += $file.Name } else { $installed += $file.Name }
}

$verb = if ($DryRun) { 'would install' } else { 'installed' }
foreach ($name in $installed) { Write-Host "  $verb   $name" }

$verb = if ($DryRun) { 'would replace' } else { 'replaced ' }
foreach ($name in $replaced)  { Write-Host "  $verb   $name" }

foreach ($name in $skipped) {
    Write-Host "  skipped         $name   (already present -- pass -Force to overwrite)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host ("  {0} new, {1} replaced, {2} skipped" -f $installed.Count, $replaced.Count, $skipped.Count)

if ($skipped.Count -eq $files.Count) {
    Write-Host "  Nothing changed. Every file was already there." -ForegroundColor Yellow
} elseif (-not $DryRun) {
    Write-Host ""
    Write-Host "  Start a new Claude Code session, then try:"
    Write-Host "    use the security-reviewer agent to audit this repository"
}
Write-Host ""
