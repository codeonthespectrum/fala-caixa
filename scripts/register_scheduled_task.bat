@echo off
REM ============================================================================
REM Registra o Agente Nex Alertas no Agendador de Tarefas do Windows (08:00 AM)
REM Executa diariamente em segundo plano no caixa/servidor local.
REM ============================================================================

SET TASK_NAME="NexAlertasInteligentesDiario"
SET EXE_PATH="%~dp0..\dist\nex-alerts.exe"
SET WORK_DIR="%~dp0.."

echo Registrando tarefa agendada %TASK_NAME% para execucao diaria as 08:00...
schtasks /Create /SC DAILY /TN %TASK_NAME% /TR "%EXE_PATH% --mode folder --path \"%WORK_DIR%\dados_nex\"" /ST 08:00 /F

echo.
echo [OK] Tarefa agendada criada com sucesso!
echo Para testar imediatamente, execute: schtasks /Run /TN %TASK_NAME%
pause
