<#
.SYNOPSIS
  Installe FAAALL-ALARM sous Windows : environnement Python, dépendances, modèle de pose et vérification.
.PARAMETER InstallEspeak
  Installe eSpeak NG avec winget s'il est absent (installation système, à lancer en connaissance de cause).
.PARAMETER PythonCommand
  Commande Python à utiliser (par défaut : py, puis python).
#>
[CmdletBinding()]
param(
    [switch]$InstallEspeak,
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

# Modèle MediaPipe Pose Landmarker « lite » (float16), version 1, source officielle de Google.
$modelUrl = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
$modelSha256 = "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"
$modelPath = Join-Path $root "models\pose_landmarker_lite.task"

if (-not $PythonCommand) {
    $PythonCommand = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
}
Write-Host "1/4 Environnement Python (.venv)"
if (-not (Test-Path -LiteralPath (Join-Path $root ".venv\Scripts\python.exe"))) {
    & $PythonCommand -m venv (Join-Path $root ".venv")
}
$python = Join-Path $root ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -e ".[dev]" --quiet
if ($LASTEXITCODE -ne 0) { throw "L'installation des dépendances a échoué." }

Write-Host "2/4 Modèle de pose MediaPipe"
if (-not (Test-Path -LiteralPath $modelPath)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $modelPath) | Out-Null
    Invoke-WebRequest -Uri $modelUrl -OutFile $modelPath
}
$actual = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $modelSha256) {
    Remove-Item -LiteralPath $modelPath
    throw "Empreinte inattendue pour le modèle de pose : fichier supprimé, relancer l'installation."
}

Write-Host "3/4 Voix eSpeak NG"
$espeakFound = (Get-Command espeak-ng -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath "C:\Program Files\eSpeak NG\espeak-ng.exe")
if (-not $espeakFound) {
    if ($InstallEspeak) {
        winget install --id eSpeak-NG.eSpeak-NG --exact --silent --accept-package-agreements --accept-source-agreements
    } else {
        Write-Warning "eSpeak NG est introuvable : la voix sera signalée « indisponible ». Relancer avec -InstallEspeak ou : winget install --id eSpeak-NG.eSpeak-NG --exact"
    }
}

Write-Host "4/4 Vérification"
& $python -m faaall_alarm --check
exit $LASTEXITCODE
