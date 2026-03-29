@echo off
setlocal enabledelayedexpansion
title ProspectorAI - DiazUX Studio

echo.
echo  ==========================================
echo   ProspectorAI - DiazUX Studio
echo  ==========================================
echo.

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python no encontrado.
    echo  Instalalo desde https://python.org
    echo  Asegurate de marcar "Add Python to PATH" al instalar.
    pause
    exit /b 1
)

:: Verificar archivos necesarios
if not exist "server.py" (
    echo  ERROR: No se encontro server.py
    echo  Ejecuta este .bat desde la carpeta del proyecto.
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo  ERROR: No se encontro requirements.txt
    pause
    exit /b 1
)

:: Entorno virtual
if not exist "venv" (
    echo  Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

:: Instalar dependencias solo si cambio requirements.txt
if not exist "venv\.installed" goto :install
fc /b requirements.txt venv\.installed >nul 2>&1
if errorlevel 1 goto :install
goto :skip_install

:install
echo  Instalando dependencias...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo  ERROR: Fallo la instalacion de dependencias.
    pause
    exit /b 1
)
python -m playwright install chromium >nul 2>&1
copy /b requirements.txt venv\.installed >nul 2>&1
echo  Dependencias OK.

:skip_install

:: Verificar puertos
netstat -an | find "LISTENING" | find ":5000" >nul 2>&1
if not errorlevel 1 echo  AVISO: Puerto 5000 ocupado.

netstat -an | find "LISTENING" | find ":8765" >nul 2>&1
if not errorlevel 1 echo  AVISO: Puerto 8765 ocupado.

:: Crear launcher auxiliar para IG Scraper
:: Esto es mas confiable que pasar "call activate.bat" como string inline a cmd /k
if exist "ig_backend.py" (
    echo @echo off                                    > _ig_launcher.bat
    echo title IG Scraper Backend :8765              >> _ig_launcher.bat
    echo cd /d "%~dp0"                               >> _ig_launcher.bat
    echo call "%~dp0venv\Scripts\activate.bat"       >> _ig_launcher.bat
    echo echo.                                       >> _ig_launcher.bat
    echo echo  IG Scraper Backend - DiazUX           >> _ig_launcher.bat
    echo echo  Puerto: http://localhost:8765          >> _ig_launcher.bat
    echo echo  Cerrando esta ventana lo detiene.     >> _ig_launcher.bat
    echo echo.                                       >> _ig_launcher.bat
    echo python ig_backend.py                        >> _ig_launcher.bat
    echo echo.                                       >> _ig_launcher.bat
    echo echo  Backend detenido.                     >> _ig_launcher.bat
    echo pause                                       >> _ig_launcher.bat

    start "IG Scraper Backend" _ig_launcher.bat
    echo  [OK] IG Scraper Backend  -  http://localhost:8765
) else (
    echo  [--] ig_backend.py no encontrado, saltando...
)

:: Abrir navegador despues de 2 segundos
start /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

echo  [OK] ProspectorAI         -  http://localhost:5000
echo.
echo  Cerrando ESTA ventana detiene ProspectorAI.
echo  La ventana del IG Scraper se cierra por separado.
echo  ==========================================
echo.

:: Servidor principal (ocupa esta ventana)
python server.py

:: Limpiar launcher auxiliar al salir
if exist "_ig_launcher.bat" del "_ig_launcher.bat" >nul 2>&1

echo.
echo  Servidor detenido.
pause
