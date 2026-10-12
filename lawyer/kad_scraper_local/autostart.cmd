@echo off
rem Автозапуск агента парсера КАД без прав администратора.
rem
rem Кладёт ярлык в личную «Автозагрузку» пользователя: при входе в Windows
rem ярлык поднимает агента через pythonw.exe в видимой сессии - иначе браузер
rem для капчи показать некому.
rem
rem Нужен, когда setup.cmd не смог завести задачу в Планировщике заданий: на
rem машинах с ограничениями политики для этого требуются права администратора.
rem
rem Запускать один раз от имени сотрудника: autostart.cmd

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Не найдено окружение .venv. Сначала запустите setup.cmd.
    pause
    exit /b 1
)

rem Имя ярлыка только латиницей: cmd.exe читает .cmd в кодировке консоли, и
rem кириллица в имени превратилась бы в мусор.
set "LINK_NAME=kad-agent.lnk"
set "AGENT_DIR=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo Ставлю автозапуск в «Автозагрузку» пользователя...
rem Ярлык запускает pythonw.exe из .venv, а рабочим каталогом задаёт папку
rem агента: без него не найдутся agent.env и logs\agent.log.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$d = $env:AGENT_DIR;" ^
    "$p = Join-Path ([Environment]::GetFolderPath('Startup')) $env:LINK_NAME;" ^
    "try {" ^
    "  $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($p);" ^
    "  $s.TargetPath = (Join-Path $d '.venv\Scripts\pythonw.exe');" ^
    "  $s.Arguments = '-m kad_scraper.agent';" ^
    "  $s.WorkingDirectory = $d; $s.WindowStyle = 7; $s.Description = 'KAD agent';" ^
    "  $s.Save()" ^
    "} catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo [СБОЙ] Не удалось создать ярлык автозапуска.
    pause
    exit /b 1
)

if not exist "%STARTUP%\%LINK_NAME%" (
    echo [СБОЙ] Ярлык не появился: %STARTUP%\%LINK_NAME%
    pause
    exit /b 1
)

echo.
echo === Готово ===
echo Ярлык: %STARTUP%\%LINK_NAME%
echo Агент запустится сам при следующем входе в Windows.
echo Проверить сейчас: run_agent.cmd
echo.
pause
endlocal
