@echo off
REM ============================================================================
REM Script de Empacotamento do Agente Local Nex Alertas em Executavel (.exe)
REM Gera dist\nex-alerts.exe sem exigir Python instalado na maquina de destino.
REM ============================================================================

echo [1/2] Instalando PyInstaller no ambiente virtual...
.venv\Scripts\pip install pyinstaller

echo [2/2] Gerando executavel unico (dist\nex-alerts.exe)...
.venv\Scripts\pyinstaller --noconfirm --onefile --clean --name nex-alerts src\nex_alerts\main.py

echo.
echo [CONCLUIDO] Executavel disponivel em: dist\nex-alerts.exe
pause
