@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Mod Manager

set "PYEXE="

for %%D in ("C:/Program Files/Python313" "C:/Program Files/Python312" "C:/Program Files/Python311" "C:/Program Files/Python310" "C:/Program Files/Python39") do (
    if not defined PYEXE (
        if exist "%%~D\python.exe" (
            "%%~D\python.exe" -c "import tkinter" >nul 2>nul && set "PYEXE=%%~D\python.exe"
        )
    )
)

if not defined PYEXE (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PYEXE (
            "%%i" -c "import tkinter" >nul 2>nul && set "PYEXE=%%i"
        )
    )
)

if not defined PYEXE goto :nopython

set "PYW=!PYEXE:python.exe=pythonw.exe!"
if not exist "!PYW!" set "PYW=!PYEXE!"
start "" "!PYW!" "%~dp0mod_manager.py"
exit /b 0

:nopython
echo.
echo   [错误] 没有找到带有 tkinter 的 Python 3。
echo.
echo   请到 https://www.python.org/downloads/ 安装 Python 3.8 以上版本，
echo   安装时保持 "tcl/tk and IDLE" 处于勾选状态。
echo.
pause
exit /b 1
