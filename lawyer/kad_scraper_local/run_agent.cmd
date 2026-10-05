@echo off
rem Запуск агента парсера КАД. Его вызывает Планировщик заданий при входе
rem в Windows, но можно запустить и вручную, чтобы проверить.
rem
rem Настройки берутся из agent.env, пароль машины лежит там же и в окно не
rem выводится.

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

rem pythonw.exe - без окна консоли: так агент не мигает в панели задач и не
rem мешает сотруднику. Всё, что нужно для разбора, пишется в logs\agent.log.
".venv\Scripts\pythonw.exe" -m kad_scraper.agent

rem Сюда попадаем, только если агент завершился с ошибкой: при нормальной
rem работе он не возвращает управление. Пишем причину в тот же журнал,
rem иначе в Планировщике заданий будет пустое «не выполнено».
echo [%date% %time%] Агент остановился с кодом %ERRORLEVEL%. >> logs\agent.log
endlocal