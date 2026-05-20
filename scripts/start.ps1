<#
.SYNOPSIS
    Development launcher for EQ2CensusBot on Windows.

.DESCRIPTION
    Opens separate terminal windows for the backend API server and the Vite
    frontend dev server.  Run from the repo root.

.PARAMETER Target
    dev   (default) — backend + frontend
    web              — backend only
    bot              — Discord bot only
    build            — build the React frontend

.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 dev
    .\scripts\start.ps1 bot
    .\scripts\start.ps1 build
#>

param(
    [string]$Target = "dev"
)

$Root = Split-Path $PSScriptRoot -Parent

function Start-Backend {
    Write-Host "Starting backend on http://localhost:8000 ..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$Root'; python -m uvicorn web.app:app --reload --port 8000"
}

function Start-Frontend {
    Write-Host "Starting frontend dev server on http://localhost:5173 ..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$Root\frontend'; npm run dev"
}

function Start-Bot {
    Write-Host "Starting Discord bot ..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$Root'; python main.py"
}

function Build-Frontend {
    Write-Host "Building frontend ..." -ForegroundColor Cyan
    Push-Location "$Root\frontend"
    npm run build
    Pop-Location
}

switch ($Target.ToLower()) {
    "dev" {
        Start-Backend
        Start-Frontend
        Write-Host ""
        Write-Host "Both servers started in new windows." -ForegroundColor Green
        Write-Host "Open http://localhost:5173 in your browser." -ForegroundColor Green
    }
    "web" {
        Start-Backend
    }
    "bot" {
        Start-Bot
    }
    "build" {
        Build-Frontend
    }
    default {
        Write-Host "Unknown target '$Target'. Use: dev | web | bot | build" -ForegroundColor Red
        exit 1
    }
}
