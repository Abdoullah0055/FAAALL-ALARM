<#
.SYNOPSIS
  Lance les tests automatisés. Ils n'utilisent ni webcam, ni Internet, ni SMS réel, ni délai réel de dix secondes.
#>
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArguments)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Environnement absent : lancer d'abord .\scripts\install.ps1" }
Set-Location -LiteralPath $root
& $python -m pytest @PytestArguments
exit $LASTEXITCODE
