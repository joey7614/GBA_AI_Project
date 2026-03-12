@echo off
echo Building pokefirered...
wsl -e bash -c "cd /mnt/c/Users/linji/Documents/GBA_AI_Project/GBA_AI_Project/pokefirered && make -j4 2>&1"
if %ERRORLEVEL% == 0 (
    echo.
    echo Build successful! ROM: pokefirered\pokefirered.gba
) else (
    echo.
    echo Build FAILED.
)
pause
