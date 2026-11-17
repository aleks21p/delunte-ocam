@echo off
rem Double-click to build dist\Haffop.exe (Windows 11 edition)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
pause
