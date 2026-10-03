@echo off
title KINO - Synchronisation temps reel Google Drive
cd /d "%~dp0"
echo ========================================================
echo   Lancement de la synchronisation automatique KINO
echo ========================================================
python sync_drive.py --watch
pause
