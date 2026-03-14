# ProspectorAI — DiazUX Studio
## Instalación en Windows (5 minutos)

### Requisitos
- Python 3.10+ (https://python.org/downloads)
- Ollama corriendo con Deepseek (https://ollama.com)

### Paso 1 — Instalar Deepseek en Ollama
Abrí una terminal (CMD o PowerShell) y ejecutá:
```
ollama pull deepseek-r1:latest
```

### Paso 2 — Iniciar Ollama con CORS habilitado
```
set OLLAMA_ORIGINS=*
ollama serve
```
(Dejá esta ventana abierta)

### Paso 3 — Iniciar la aplicación
Doble click en `INICIAR.bat`

Se abre el navegador automáticamente en http://localhost:5000

---

## Configuración inicial

1. Ir a **Ajustes** en el sidebar
2. Configurar **Ollama URL**: `http://localhost:11434` y **Modelo**: `deepseek-r1:latest`
3. Configurar **SMTP** con los datos de tu email:
   - Servidor: el de tu proveedor de hosting (ej: mail.diazux.tech)
   - Puerto: 587
   - Email y contraseña
4. Completar **Tu perfil** con nombre, empresa y caso de éxito

---

## Funciones

| Función | Descripción |
|---------|-------------|
| Email Finder | Busca emails por dominio o por nombre + empresa usando Deepseek |
| Verificador SMTP | Verifica si el email existe (SMTP real + IA) |
| Scraper | Genera listas de prospectos por rubro con Deepseek |
| Auditoría Web | Analiza el sitio del prospecto y genera el hook para el email |
| CRM | Gestión completa de prospectos con estados y scoring |
| Pipeline | Vista del estado de ventas con valor de deals |
| Secuencias | Genera emails personalizados con Deepseek |
| Tracking | Pixel de seguimiento en cada email para saber quién abrió |
| Reportes | Analytics de aperturas, funnel y conversión |
| HubSpot | Exporta contactos directamente via API |

---

## Datos
Todos los datos se guardan en `prospector.db` (SQLite) en la misma carpeta.
Para hacer backup, copiá ese archivo.
