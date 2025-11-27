@echo off
chcp 65001 >nul
echo.
echo 🚀 Python Project Bootstrap — универсальная система развёртывания
echo ============================================================

REM Автоопределение Python (3.8+)
set PYTHON=python
%PYTHON% --version >nul 2>&1
if %errorlevel% neq 0 (
    set PYTHON=python3
    %PYTHON% --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo ❌ Python не найден. Установите Python 3.8+ https://python.org
        pause & exit /b 1
    )
)

REM Запуск главного скрипта
%PYTHON% pydeploy.py %*
if %errorlevel% equ 0 (
    echo.
    echo ✅ Проект успешно развёрнут!
) else (
    echo.
    echo ❌ Ошибка развёртывания. Подробности выше.
)
pause