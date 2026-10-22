@echo off
rem Double-click entry point; all work is in gen_product.py. Arguments pass through.
rem Pauses only when started without arguments, so the result can be read.

python "%~dp0gen_product.py" %*
set RC=%ERRORLEVEL%
if "%~1"=="" (
    echo.
    pause
)
exit /b %RC%
