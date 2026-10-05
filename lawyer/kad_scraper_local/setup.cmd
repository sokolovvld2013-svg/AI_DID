@echo off
rem Установка агента парсера КАД на компьютере сотрудника.
rem
rem Скрипт делает три вещи:
rem   1. Создаёт окружение и ставит зависимости.
rem   2. Создаёт agent.env, если его ещё нет, и просит заполнить.
rem   3. Регистрирует автозапуск в Планировщике заданий при входе в Windows.
rem
rem Запускать нужно один раз, от имени сотрудника, в обычном окне cmd:
rem     setup.cmd
rem
rem Права администратора НЕ нужны: агент работает в сессии пользователя,
rem иначе не будет видного окна браузера для разгадывания капчи.

setlocal
cd /d "%~dp0"

echo.
echo === Установка агента парсера КАД ===
echo.

rem ---------- 1. Окружение и зависимости ----------
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создаю окружение...
    where py >nul 2>&1 && ( py -3.11 -m venv .venv ) || ( python -m venv .venv )
    if not exist ".venv\Scripts\python.exe" (
        echo [СБОЙ] Не удалось создать окружение. Установите Python 3.11+.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Окружение уже есть.
)

echo [1/3] Ставлю зависимости...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements-agent.txt
if errorlevel 1 (
    echo [СБОЙ] Не удалось поставить зависимости.
    pause
    exit /b 1
)

rem ---------- 2. Браузер ----------
rem Playwright тянет свой Chromium, но он ставится не всегда. Агент умеет
rem работать через системный Chrome или Edge, поэтому проверяем оба пути и
rem ругаемся только если нет ни одного.
echo [1/3] Проверяю браузер...
".venv\Scripts\python.exe" -m playwright install chromium >nul 2>&1
if exist "%LOCALAPPDATA%\ms-playwright" (
    echo       Playwright Chromium на месте.
) else (
    echo       Chromium Playwright не поставился - нужен системный Chrome или Edge.
)

rem ---------- 3. Настройки ----------
if not exist "agent.env" (
    echo [2/3] Создаю agent.env - впишите адрес сервера и пароль машины.
    copy /y "agent.env.example" "agent.env" >nul
    notepad agent.env
    echo       Откроется блокнот. Впишите KAD_AGENT_SERVER, KAD_AGENT_TOKEN
    echo       и KAD_AGENT_NAME, сохраните и нажмите Enter здесь.
    pause
) else (
    echo [2/3] Файл agent.env уже есть.
)

rem ---------- 4. Автозапуск ----------
set "TASKNAME=Парсер КАД (агент)"
echo [3/3] Настраиваю автозапуск при входе в Windows...

rem Удаляем прежнюю задачу, чтобы регистрация была идемпотентной:
rem повторный запуск setup.cmd не должен плодить дубликаты.
schtasks /query /tn "%TASKNAME%" >nul 2>&1 && schtasks /delete /tn "%TASKNAME%" /f >nul 2>&1

rem Запуск только при входе сотрудника и только в интерактивной сессии.
rem pythonw - окна консоли нет, агент не мигает в панели задач.
rem Если пользователь заблокирует экран или уйдёт в сон, видимого браузера
rem не будет и капчу решить нечем - поэтому такие машины заданиями лучше
rem не занимать.
schtasks /create /tn "%TASKNAME%" /tr "cmd /c \"\"%~dp0run_agent.cmd\"\"" /sc onlogon /rl limited /f >nul
if errorlevel 1 (
    echo [СБОЙ] Не удалось создать задачу в Планировщике заданий.
    pause
    exit /b 1
)

echo.
echo === Готово ===
echo.
echo Агент запустится сам при следующем входе в Windows.
echo Проверить прямо сейчас: запустите  run_agent.cmd
echo Задания появятся во вкладке "Арбитражные дела" блока "Юрист".
echo.
echo Если что-то не работает, смотрите журнал: logs\agent.log
echo.
pause
endlocal