@echo off
REM ============================================================
REM  STOCK BOT - Avvio dashboard web in locale
REM  Doppio click su questo file:
REM    1) apre questo terminale e avvia il server (uvicorn)
REM    2) apre automaticamente il browser su http://localhost:8000
REM  Chiudi questa finestra (o premi CTRL+C) per fermare il server.
REM ============================================================

title Stock Bot - Server locale
cd /d "%~dp0"

set "PY=C:\Users\Mattia\AppData\Local\Programs\Python\Python312\python.exe"
set "PORT=8000"
set "URL=http://localhost:%PORT%/v2"

REM --- Le chiavi API: python-dotenv cerca un file ".env", ma qui si chiama ".env.txt".
REM     Se manca ".env" lo creo copiando ".env.txt" (cosi' GROQ/Telegram funzionano in locale).
if not exist ".env" if exist ".env.txt" copy ".env.txt" ".env" >nul

REM --- Controllo che Python esista nel percorso atteso, altrimenti provo il PATH di sistema.
if not exist "%PY%" set "PY=python"

REM --- Apre il browser dopo 4 secondi (in parallelo), il tempo che il server parta.
start "" cmd /c "timeout /t 4 /nobreak >nul & start %URL%"

echo ============================================================
echo   STOCK BOT - Dashboard web
echo   Server in avvio su:  %URL%
echo   (chiudi questa finestra per fermare il server)
echo ============================================================
echo.

"%PY%" -m uvicorn web_server:app --host 127.0.0.1 --port %PORT%

echo.
echo Il server si e' fermato.
pause
