@echo off
rem Codex runs hooks through the user shell. A quoted python.exe path fails there on Windows.
"C:\Program Files\Python311\python.exe" "%~dp0jev_effort_hook.py" %*
