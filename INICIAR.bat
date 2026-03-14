@echo off
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
    pause
    exit
)

:: Instalar dependencias si no existen
if not exist "venv" (
    echo  Creando entorno virtual...
    python -m venv venv
)

call venv\Scripts\activate

echo  Instalando dependencias...
pip install -r requirements.txt -q

echo.
echo  Iniciando servidor en http://localhost:5000
echo  Abriendo navegador...
echo.

:: Abrir navegador despues de 2 segundos
start /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

:: Iniciar servidor
python server.py

pause
