# JobPilot Auto - модуль автозаполнения форм. Запуск: двойной клик по install-forms.bat
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== JobPilot Auto: автозаполнение форм ==" -ForegroundColor Cyan

$py = "py -3"
$pyFile = Join-Path $root "scripts\python.txt"
if (Test-Path $pyFile) { $py = (Get-Content $pyFile -Raw).Trim() }
Write-Host "Python: $py"

Write-Host "Ставлю Playwright (управление браузером; сам браузер не скачивается - используется твой Edge)..."
& cmd /c "$py -m pip install --user --upgrade --disable-pip-version-check playwright"
if ($LASTEXITCODE -ne 0) { Write-Host "Не получилось поставить Playwright - напиши Claude, что вывелось выше." -ForegroundColor Red; exit 1 }

Write-Host "Проверяю, что Edge запускается..."
$check = "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(channel='msedge', headless=True); print('Edge', b.version); b.close(); p.stop()"
& cmd /c "$py -c ""$check"""
if ($LASTEXITCODE -ne 0) {
    Write-Host "Edge не запустился. Если у тебя Chrome, открой config.toml и поставь в [forms] browser = ""chrome""." -ForegroundColor Yellow
}

Write-Host "Проверка программы (тесты)..."
& cmd /c "$py -m unittest discover -s tests"
if ($LASTEXITCODE -ne 0) { Write-Host "Тесты не прошли - напиши Claude, что вывелось выше." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Готово. Как пользоваться:" -ForegroundColor Green
Write-Host " - todo.bat: для вакансий с формой на сайте нажми Enter - программа откроет форму в Edge,"
Write-Host "   заполнит поля, загрузит Anschreiben и резюме и покажет, что осталось тебе."
Write-Host " - Отправка только после твоего Enter. Капчу делаешь сам; галочку обязательного согласия программа ставит сама (Talentpool - никогда)."
Write-Host " - Одна вакансия: jp fill ID"
