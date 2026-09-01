\
param([Parameter(Mandatory=$true)][string]$RepoUrl)
$ErrorActionPreference = "Stop"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "Git is not installed. Install Git for Windows first." -ForegroundColor Red
  exit 1
}
git init
git branch -M main
git add .
git commit -m "Initial Aura Voucher WiFi starter"
try { git remote remove origin 2>$null } catch {}
git remote add origin $RepoUrl
git push -u origin main
Write-Host "Pushed to GitHub successfully." -ForegroundColor Green
