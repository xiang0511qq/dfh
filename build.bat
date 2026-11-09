@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul || (echo 未找到 Python，请先安装 Python 3.10 或更高版本。 & pause & exit /b 1)
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --clean --onefile --windowed --name "二维码生成工具" qr_generator.py
if exist "dist\二维码生成工具.exe" (echo. & echo 已生成：dist\二维码生成工具.exe) else (echo 打包失败)
pause
