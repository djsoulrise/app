@echo off
cd /d "%~dp0"
if not exist build_env (py -3.12 -m venv build_env || goto :err)
build_env\Scripts\python -m pip install -r requirements.txt pywebview pyinstaller || goto :err
build_env\Scripts\python -m PyInstaller --noconfirm --clean "DJ asistent.spec" || goto :err
echo Gotovo: dist\DJ asistent.exe
pause
exit /b 0
:err
echo Greska pri pravljenju.
pause
exit /b 1
