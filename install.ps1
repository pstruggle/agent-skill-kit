#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Install the skills in this repository into one or more agent skill directories.

.DESCRIPTION
    Copies (or links) every skill under ./skills into the skill directory used by
    DSH, Codex, Claude Code, or the shared .agents convention.

    Targets and destinations:
      dsh     user:    $DSH_HOME/skills               (default ~/.dsh/skills)
      dsh     project: <project>/.dsh/skills
      codex   both:    $CODEX_HOME/skills             (default ~/.codex/skills)
      claude  user:    ~/.claude/skills
      claude  project: <project>/.claude/skills
      agents  user:    ~/.agents/skills
      agents  project: <project>/.agents/skills

    Codex discovers skills from CODEX_HOME only, so -Scope project still installs
    into $CODEX_HOME/skills. Pass -Dest to also install into a custom directory.

.EXAMPLE
    pwsh ./install.ps1 -List

.EXAMPLE
    pwsh ./install.ps1 -Target all -Scope user

.EXAMPLE
    pwsh ./install.ps1 -Target dsh -Scope project -ProjectRoot C:\work\my-app

.EXAMPLE
    pwsh ./install.ps1 -Target codex,claude -Mode link

.EXAMPLE
    pwsh ./install.ps1 -Uninstall -Target claude -Skill project-task-sop
#>
[CmdletBinding()]
param(
    # Agent skill directories to install into. 'all' covers every built-in target.
    [ValidateSet('dsh', 'codex', 'claude', 'agents', 'custom', 'all')]
    [string[]]$Target = @('all'),

    # 'user' writes to the tool's home directory, 'project' writes under -ProjectRoot.
    [ValidateSet('user', 'project')]
    [string]$Scope = 'user',

    # Skill names to install. Empty means every skill under ./skills.
    [string[]]$Skill = @(),

    # 'copy' duplicates the files; 'link' creates a junction that tracks ./skills.
    [ValidateSet('copy', 'link')]
    [string]$Mode = 'copy',

    # Project root used by -Scope project.
    [string]$ProjectRoot = (Get-Location).Path,

    # Extra destination root; each skill lands in <Dest>/<skill>.
    [string]$Dest,

    [switch]$Force,
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$List
)

$ErrorActionPreference = 'Stop'

$repoRoot = $PSScriptRoot
$sourceRoot = Join-Path $repoRoot 'skills'

function Join-Parts {
    param([string]$Base, [string[]]$Parts)
    $path = $Base
    foreach ($part in $Parts) { $path = Join-Path $path $part }
    return $path
}

function Get-AgentHome {
    param([string]$EnvName, [string]$Fallback)
    $value = [Environment]::GetEnvironmentVariable($EnvName)
    if ([string]::IsNullOrWhiteSpace($value)) { return (Join-Path $HOME $Fallback) }
    return $value
}

function Get-AvailableSkills {
    if (-not (Test-Path -LiteralPath $sourceRoot)) {
        throw "Missing skills directory: $sourceRoot"
    }
    $found = @()
    foreach ($dir in (Get-ChildItem -LiteralPath $sourceRoot -Directory | Sort-Object Name)) {
        if (Test-Path -LiteralPath (Join-Path $dir.FullName 'SKILL.md')) { $found += $dir }
    }
    return $found
}

function Assert-UnderRoot {
    param([string]$Path, [string]$Root)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $prefix = $fullRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch '$fullPath' because it is outside '$fullRoot'."
    }
}

$dshHome = Get-AgentHome -EnvName 'DSH_HOME' -Fallback '.dsh'
$codexHome = Get-AgentHome -EnvName 'CODEX_HOME' -Fallback '.codex'

$roots = [ordered]@{}
if ($Scope -eq 'project') {
    $roots['dsh'] = Join-Parts $ProjectRoot @('.dsh', 'skills')
    $roots['agents'] = Join-Parts $ProjectRoot @('.agents', 'skills')
    $roots['claude'] = Join-Parts $ProjectRoot @('.claude', 'skills')
    $roots['codex'] = Join-Parts $codexHome @('skills')
}
else {
    $roots['dsh'] = Join-Parts $dshHome @('skills')
    $roots['agents'] = Join-Parts $HOME @('.agents', 'skills')
    $roots['claude'] = Join-Parts $HOME @('.claude', 'skills')
    $roots['codex'] = Join-Parts $codexHome @('skills')
}
if ($Dest) { $roots['custom'] = $Dest }

if ($Target -contains 'all') {
    $selected = @($roots.Keys)
}
else {
    $selected = @($Target)
    if ($Dest -and ($selected -notcontains 'custom')) { $selected += 'custom' }
}

if (($selected -contains 'custom') -and (-not $Dest)) {
    throw "The 'custom' target needs -Dest <path>."
}

$available = @(Get-AvailableSkills)

if ($Skill.Count -gt 0) {
    $wanted = @($available | Where-Object { $Skill -contains $_.Name })
    $missing = @($Skill | Where-Object { $available.Name -notcontains $_ })
    if ($missing.Count -gt 0) { throw "Unknown skill(s): $($missing -join ', ')" }
    $available = $wanted
}

if ($List) {
    Write-Host ''
    Write-Host 'Skills in this repository:'
    foreach ($item in $available) { Write-Host ("  - {0}" -f $item.Name) }
    Write-Host ''
    Write-Host ("Install destinations (scope: {0}):" -f $Scope)
    foreach ($name in $selected) { Write-Host ("  {0,-7} {1}" -f $name, $roots[$name]) }
    Write-Host ''
    exit 0
}

if ($available.Count -eq 0) { throw 'No skills to install.' }

$verb = if ($Uninstall) { 'Uninstall' } else { 'Install' }
$planned = 0
$skipped = 0

foreach ($item in $available) {
    foreach ($name in $selected) {
        $root = $roots[$name]
        $targetPath = Join-Path $root $item.Name
        Assert-UnderRoot -Path $targetPath -Root $root

        if ($Uninstall) {
            if (-not (Test-Path -LiteralPath $targetPath)) {
                Write-Host ("[{0}] {1}: not installed" -f $name, $item.Name)
                $skipped++
                continue
            }
            Write-Host ("[{0}] remove  {1}" -f $name, $targetPath)
            if (-not $DryRun) { Remove-Item -LiteralPath $targetPath -Recurse -Force }
            $planned++
            continue
        }

        if (Test-Path -LiteralPath $targetPath) {
            if (-not $Force) {
                Write-Host ("[{0}] {1}: already exists, skipped (use -Force to replace)" -f $name, $item.Name)
                $skipped++
                continue
            }
            Write-Host ("[{0}] replace {1}" -f $name, $targetPath)
            if (-not $DryRun) { Remove-Item -LiteralPath $targetPath -Recurse -Force }
        }
        else {
            Write-Host ("[{0}] {1,-4} {2}" -f $name, $Mode, $targetPath)
        }

        if ($DryRun) { $planned++; continue }

        $null = New-Item -ItemType Directory -Force -Path $root
        if ($Mode -eq 'link') {
            $null = New-Item -ItemType Junction -Path $targetPath -Target $item.FullName
        }
        else {
            Copy-Item -LiteralPath $item.FullName -Destination $targetPath -Recurse -Force
        }
        $planned++
    }
}

$suffix = if ($DryRun) { ' (dry run, nothing written)' } else { '' }
Write-Host ''
Write-Host ("{0}: {1} action(s), {2} skipped{3}" -f $verb, $planned, $skipped, $suffix)
Write-Host 'Restart the agent session so it picks up the new skill.'
