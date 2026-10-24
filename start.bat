@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
  echo [!] Net faila .env. Pereimenuj .env.example v .env i vstav token ot BotFather.
  pause
  exit /b
)
echo Ustanavlivayu biblioteki...
python -m pip install -r requirements.txt
echo.
echo Zapuskayu bota. Chtoby ostanovit - nazhmi Ctrl+C
python bot.py
pause
