@echo off
chcp 65001 >nul
title KINO Desktop
cd /d "%~dp0"

echo ========================================================
echo                 LANCEMENT DE KINO (Windows)
echo ========================================================
echo.

:: 1. Verifier si Python est installe
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    py --version >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set PYTHON_CMD=py
    ) else (
        echo [ERREUR] Python 3 n'est pas detecte dans votre PATH.
        echo.
        echo Veuillez installer Python depuis https://www.python.org/downloads/
        echo IMPORTANT : Pensez a bien cocher "Add Python to PATH" lors de l'installation.
        echo.
        pause
        exit /b 1
    )
) else (
    set PYTHON_CMD=python
)

:: 2. Configuration environnement virtuel local (.venv_win)
if not exist ".venv_win" (
    echo [1/3] Creation de l'environnement virtuel local (.venv_win)...
    %PYTHON_CMD% -m venv .venv_win
    if %ERRORLEVEL% neq 0 (
        echo [ERREUR] Impossible de creer l'environnement virtuel.
        pause
        exit /b 1
    )
    echo [2/3] Installation des dependances (pywebview, etc.)...
    call .venv_win\Scripts\activate.bat
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt --quiet
    echo.
    echo [OK] Installation terminee avec succes !
    echo.
) else (
    call .venv_win\Scripts\activate.bat
)

:: 3. Demarrage de l'application
echo [3/3] Demarrage de KINO Desktop...
python desktop.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo L'application s'est fermee avec un code d'erreur (%ERRORLEVEL%).
    pause
)
