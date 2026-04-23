@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Uso: doble click o ejecutar en CMD dentro del repo.
REM Objetivo: crear rama work (si no existe), commitear y pushear.

echo ==========================================
echo   ProspectorAI - Commit + Push (work)
echo ==========================================

for /f %%i in ('git rev-parse --is-inside-work-tree 2^>nul') do set INSIDE=%%i
if not "%INSIDE%"=="true" (
  echo [ERROR] Esta carpeta no es un repo git.
  pause
  exit /b 1
)

git fetch --all --prune

REM Crear/cambiar a work
for /f "tokens=*" %%b in ('git branch --list work') do set HAS_WORK=%%b
if "%HAS_WORK%"=="" (
  echo [INFO] Creando rama work...
  git checkout -b work
) else (
  echo [INFO] Cambiando a rama work...
  git checkout work
)

echo.
git status --short

echo.
set /p MSG=Mensaje de commit (Enter = "update local changes"): 
if "%MSG%"=="" set MSG=update local changes

git add .
git diff --cached --quiet
if %ERRORLEVEL% EQU 0 (
  echo [INFO] No hay cambios para commitear.
) else (
  git commit -m "%MSG%"
)

echo.
echo [INFO] Haciendo push a origin/work...
git push -u origin work
if %ERRORLEVEL% NEQ 0 (
  echo [WARN] Fallo el push. Verifica remote/auth/reglas del repo.
  pause
  exit /b 1
)

echo.
echo [OK] Listo. Rama work actualizada.
pause
exit /b 0
