@echo off
rem Перезапуск агента парсера КАД после правки кода.
rem
rem Нужен потому, что запущенный агент держит модули в памяти: правку в
rem kad_scraper\ он сам не подхватит, пока его не перезапустить.
rem
rem Что делает:
rem   1. Останавливает агента вместе с браузером, который он успел открыть.
rem   2. Запускает агента заново через run_agent.cmd.
rem   3. Ждёт, пока агент поднимется, и пишет результат в logs\restart.log.
rem
rem Переустановка не нужна: setup.cmd нужен только при первой установке или
rem после правки requirements-agent.txt.

rem Кириллица здесь записана в UTF-8, как и в run_agent.cmd. Кодовую страницу
rem внутри .cmd менять нельзя: после chcp cmd продолжает читать файл в новой
rem кодировке и путает многобайтные символы - скрипт рассыпается на команды.
rem Правильный вывод даёт консоль Windows, где уже выставлен UTF-8.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Не найдено окружение. Запустите setup.cmd.
    pause
    exit /b 1
)

if not exist "agent.env" (
    echo Нет файла agent.env. Запустите setup.cmd.
    pause
    exit /b 1
)

rem Отметку о перезапуске пишем в отдельный logs\restart.log, а не в
rem logs\agent.log: работающий агент держит agent.log открытым, и дописать в
rem него нельзя - cmd вернёт «The process cannot access the file».
if not exist "logs" mkdir "logs"
echo [%date% %time%] Запуск restart_agent.cmd >> logs\restart.log

rem --- 1. Останавливаем работающий агент ----------------------------------
rem Ищем по командной строке, а не по имени pythonw.exe: последним на машине
rem может пользоваться что угодно ещё. Агента видно по двум процессам -
rem .venv\Scripts\pythonw.exe лишь запускает настоящий интерпретатор, поэтому
rem командная строка совпадает у обоих. taskkill /T сносит и Chromium.
rem
rem Паузу делаем через PowerShell, а не через timeout: timeout требует
rem консоли и в перенаправленном вводе падает с «Input redirection is not
rem supported», из-за чего проверка в конце даёт ложное «агент не поднялся».
echo Останавливаю агента...
powershell -NoProfile -Command "$p = @(Get-CimInstance Win32_Process -Filter 'Name = ''pythonw.exe''' | Where-Object { $_.CommandLine -like '*kad_scraper.agent*' }); if (-not $p) { Write-Host '   Агент не был запущен' } else { $p | ForEach-Object { Write-Host ('   PID ' + $_.ProcessId); taskkill /PID $_.ProcessId /T /F *> $null } }; Start-Sleep -Seconds 2"

rem --- 2. Запускаем заново -------------------------------------------------
rem Через run_agent.cmd, а не через задачу Планировщика: та задача создаётся
rem на автозапуск при установке, и на машинах без неё её нет, а повторный
rem её запуск добавил бы вторую копию агента.
echo Запускаю агента...
start "" cmd /c "run_agent.cmd"

rem --- 3. Проверяем, что агент поднялся -------------------------------------
rem Агент поднимается не мгновенно: cmd, лаунчер venv и импорт playwright
rem занимают несколько секунд, поэтому опрашиваем процесс с паузами.
set /a ATTEMPT=0
:wait_agent
set /a ATTEMPT+=1
powershell -NoProfile -Command "Start-Sleep -Seconds 2; if (Get-CimInstance Win32_Process -Filter 'Name = ''pythonw.exe''' | Where-Object { $_.CommandLine -like '*kad_scraper.agent*' }) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto agent_up
if %ATTEMPT% lss 8 goto wait_agent

echo   ВНИМАНИЕ: агент не поднялся за 16 с. Смотрите logs\agent.log
echo Не удалось поднять агента >> logs\restart.log
pause
exit /b 1

:agent_up
echo.
echo Готово. Агент работает с новым кодом.
echo Журнал: logs\agent.log
echo Агент перезапущен >> logs\restart.log
echo.
exit /b 0