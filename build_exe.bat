@echo off
cd /d "%~dp0"
echo Building Apichat.exe ...
pip install pyinstaller --quiet
python -m PyInstaller --noconfirm --onefile --windowed --name "Apichat" --add-data "web;web" --add-data "app/files/fonts;app/files/fonts" --collect-all webview --collect-submodules uvicorn --hidden-import uvicorn.logging --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.lifespan.on --hidden-import uvicorn.loops.auto main.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)
copy /Y "dist\Apichat.exe" "%USERPROFILE%\Desktop\Apichat.exe"
echo.
echo Done! "Apichat.exe" is on your Desktop.
pause
