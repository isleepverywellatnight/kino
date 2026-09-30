@echo off
chcp 65001 >nul
title KINO Desktop
cd /d "%~dp0"

echo ========================================================
echo                 LANCEMENT DE KINO (Windows)
echo ========================================================
echo.

:: 1. Verifier si Python est installe
set PYTHON_CMD=python
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
)

:: 2. Verifier si pywebview est deja disponible directement
%PYTHON_CMD% -c "import webview" >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Environnement Python detecte avec succes.
    echo Demarrage de KINO Desktop...
    %PYTHON_CMD% desktop.py
    goto FIN
)

:: 3. Sinon, utiliser ou creer l'environnement virtuel local (.venv_win)
if not exist ".venv_win" (
    echo Creation de l'environnement virtuel local (.venv_win)...
    %PYTHON_CMD% -m venv .venv_win
    if %ERRORLEVEL% neq 0 (
        echo [ERREUR] Impossible de creer l'environnement virtuel.
        pause
        exit /b 1
    )
    echo Installation des dependances (pywebview, etc.)...
    call .venv_win\Scripts\activate.bat
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt --quiet
    echo [OK] Installation terminee avec succes !
) else (
    call .venv_win\Scripts\activate.bat
)

echo Demarrage de KINO Desktop...
python desktop.py

:FIN
if %ERRORLEVEL% neq 0 (
    echo.
    echo L'application s'est fermee avec un code d'erreur (%ERRORLEVEL%).
    pause
)
