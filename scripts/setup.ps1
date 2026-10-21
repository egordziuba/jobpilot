# JobPilot Auto - установка. Запуск: двойной клик по setup.bat
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== JobPilot Auto: установка ==" -ForegroundColor Cyan

$py = $null
$v = ""
foreach ($cand in @("py -3", "python")) {
    try {
        $v = & cmd /c "$cand -c ""import sys;print(sys.version_info[0]*100+sys.version_info[1])""" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and ([int]$v.Trim() -ge 311)) { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Host "Нужен Python 3.11 или новее. Установи: winget install Python.Python.3.12  и запусти setup.bat снова." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $py ($v)"
Set-Content -Path (Join-Path $root "scripts\python.txt") -Value $py -Encoding ascii

New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
foreach ($pair in @(@("config.example.toml", "config.toml"), @("profile\facts.example.toml", "profile\facts.toml"))) {
    if (-not (Test-Path (Join-Path $root $pair[1]))) {
        Copy-Item (Join-Path $root $pair[0]) (Join-Path $root $pair[1])
        Write-Host "Создан $($pair[1]) из примера - впиши свои данные." -ForegroundColor Yellow
    }
}
New-Item -ItemType Directory -Force -Path (Join-Path $root "data\manual") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root "documents") | Out-Null
if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "Создан файл .env - впиши в него пароль приложения Gmail (SMTP_PASSWORD)." -ForegroundColor Yellow
}

Write-Host "Проверка программы (тесты)..."
& cmd /c "$py -m unittest discover -s tests" 
if ($LASTEXITCODE -ne 0) { Write-Host "Тесты не прошли - напиши Claude, что вывелось выше." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Готово. Дальше:" -ForegroundColor Green
Write-Host " 1) Открой .env и впиши SMTP_PASSWORD (пароль приложения Google)."
Write-Host " 2) Двойной клик test-mail.bat - придёт тестовое письмо."
Write-Host " 3) Двойной клик run.bat - первый пробный прогон, сводка придёт на почту."
Write-Host " 4) Двойной клик install-task.bat - запуск каждый день в 08:15 и 15:15."
Start-Process notepad.exe (Join-Path $root ".env")
