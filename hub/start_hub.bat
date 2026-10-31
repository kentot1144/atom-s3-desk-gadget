@echo off
rem Silent launcher: hands off to the VBS so no console window stays open.
start "" /b wscript.exe "%~dp0start_hub_silent.vbs"
exit /b 0
