@'
param(
    [Parameter(Mandatory=$true)]
    [string]$RepoUrl
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Git is not installed." -ForegroundColor Red
    exit 1
}

git init
git branch -M main
git add .
git commit -m "Initial Aura Voucher WiFi starter"

if (git remote | Select-String "^origin$") {
    git remote set-url origin $RepoUrl
} else {
    git remote add origin $RepoUrl
}

git push -u origin main

if ($LASTEXITCODE -ne 0) {
    throw "Git push failed."
}

Write-Host "Pushed to GitHub successfully." -ForegroundColor Green
'@ | Set-Content .\scripts\setup-repo.ps1