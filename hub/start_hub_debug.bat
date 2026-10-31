@echo off
rem Prefer start_hub_silent.vbs (no window at all).
rem This file exists for people who want a visible log.
cd /d "%~dp0"
python hub.py
pause
