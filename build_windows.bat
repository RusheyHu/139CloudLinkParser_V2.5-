@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [1/4] Creating build environment...
py -3 -m venv .venv
if errorlevel 1 goto error
call .venv\Scripts\activate.bat
echo [2/4] Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 goto error
echo [3/4] Building Windows application...
pyinstaller --noconfirm --clean --windowed --name 139CloudLinkParserV2_5 --collect-all PySide6 --collect-all playwright main.py
if errorlevel 1 goto error
echo [4/4] Done.
echo EXE: dist\139CloudLinkParserV2_5\139CloudLinkParserV2_5.exe
echo Note: Install Microsoft Edge or Google Chrome before using QR login.
pause
exit /b 0
:error
echo Build failed. Please check the error messages above.
pause
exit /b 1
