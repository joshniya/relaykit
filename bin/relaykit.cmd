@echo off
rem relaykit wrapper for Windows shells (the Claude Code plugin's bin/ folder is on PATH).
rem Prefers an installed relaykit; falls back to the copy bundled with the plugin.
setlocal
set "RK_SRC=%~dp0..\src\relaykit\__main__.py"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -c "import relaykit" >nul 2>nul && (py -3 -m relaykit %* & exit /b %ERRORLEVEL%)
  py -3 "%RK_SRC%" %*
  exit /b %ERRORLEVEL%
)
python -c "import relaykit" >nul 2>nul && (python -m relaykit %* & exit /b %ERRORLEVEL%)
python "%RK_SRC%" %*
exit /b %ERRORLEVEL%
