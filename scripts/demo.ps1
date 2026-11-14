<#
.SYNOPSIS
  Démo reproductible, sans clé API ni SMS réel : rejoue un MP4 de mannequins, déclenche la voix,
  affiche le décompte, puis simule le SMS et la réponse « Je suis en route, j'arrive dans environ 20 minutes ».
.PARAMETER Scenario
  Scénario à rejouer (voir -List). Par défaut : chute_debout.
.PARAMETER Cancel
  Annule l'alerte après N secondes pour montrer l'annulation.
.PARAMETER Headless
  Rejoue le scénario sans fenêtre et affiche la chronologie.
.PARAMETER List
  Liste les scénarios disponibles.
#>
[CmdletBinding()]
param(
    [string]$Scenario = "chute_debout",
    [Nullable[double]]$Cancel = $null,
    [switch]$Headless,
    [switch]$List,
    [switch]$NoReply
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Environnement absent : lancer d'abord .\scripts\install.ps1" }
Set-Location -LiteralPath $root

if ($List) { & $python -m faaall_alarm --list-scenarios; exit $LASTEXITCODE }
$arguments = @("-m", "faaall_alarm", "--demo", $Scenario)
if ($null -ne $Cancel) { $arguments += @("--cancel-after", $Cancel) }
if ($NoReply) { $arguments += "--no-reply" }
if ($Headless) { $arguments += "--headless" }
& $python @arguments
exit $LASTEXITCODE
