@echo off
chcp 65001 >nul
cd /d "%~dp0"
:basla
echo [%time%] Verici baslatiliyor...
python verici.py --tunel --genislik 480 --kalite 40 --fps 6
echo.
echo Verici durdu, 5 saniye sonra yeniden baslatilacak (kapatmak icin bu pencereyi kapatin).
timeout /t 5 /nobreak >nul
goto basla
