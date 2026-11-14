<#
.SYNOPSIS
  Lance l'application de surveillance. Sans argument : webcam 0, SMS simulé par défaut.
.EXAMPLE
  .\scripts\run.ps1
  .\scripts\run.ps1 -Video "C:\Mes vidéos\essai.mp4"
  .\scripts\run.ps1 -Camera 1 -Countdown 15
  .\scripts\run.ps1 -Video "C:\Mes vidéos\essai.mp4" -Diagnostic "artifacts\diagnostic.jsonl"
#>
[CmdletBinding()]
param(
    [string]$Video = "",
    [Nullable[int]]$Camera = $null,
    [Nullable[double]]$Countdown = $null,
    [string]$Diagnostic = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Environnement absent : lancer d'abord .\scripts\install.ps1" }

$arguments = @("-m", "faaall_alarm")
if ($Video) { $arguments += @("--video", $Video) }
if ($null -ne $Camera) { $arguments += @("--camera", $Camera) }
if ($null -ne $Countdown) { $arguments += @("--countdown", $Countdown) }
if ($Diagnostic) { $arguments += @("--diagnostic", $Diagnostic) }
Set-Location -LiteralPath $root
& $python @arguments
exit $LASTEXITCODE
