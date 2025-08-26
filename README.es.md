# Constelación Arthexis

## Propósito
Arthexis Constellation es una suite basada en Django que centraliza herramientas para gestionar infraestructura de carga y servicios relacionados.

## Instalación
1. Clonar el repositorio: `git clone <repository_url>`
2. Entrar en el directorio del proyecto: `cd arthexis`
3. *(Opcional)* Crear y activar un entorno virtual: `python -m venv .venv` y `source .venv/bin/activate`
4. Instalar dependencias: `pip install -r requirements.txt`

## Scripts de Shell
El proyecto incluye scripts de ayuda para agilizar el desarrollo:
- `install.sh` – crea un entorno virtual, instala dependencias y opcionalmente configura un servicio systemd con `--service NOMBRE`.
- `start.sh [puerto]` – inicia el servidor de desarrollo en el puerto indicado (por defecto `8888`).
- `stop.sh [puerto|--all]` – detiene el servidor en el puerto dado o todos los servidores en ejecución.
- `command.sh <comando> [args...]` – ejecuta comandos de administración usando nombres con guiones (por ejemplo, `./command.sh show-migrations`).
- `dev-maintenance.sh` – instala dependencias actualizadas cuando cambian los requisitos y realiza tareas de mantenimiento de la base de datos.
- `upgrade.sh` – obtiene el último código y reinstala dependencias cuando es necesario.

## Tareas de VS Code
El archivo `.vscode/tasks.json` provee dos tareas:

- **Dev: maintenance** – ejecuta `dev-maintenance.sh` (o el `.bat` equivalente en Windows).
- **Update requirements** – instala dependencias actualizadas mediante `install.sh` y regenera `requirements.txt`.

## Aplicaciones públicas del sitio
Solo se enumeran las aplicaciones con fixtures de sitio y vistas públicas.

| Aplicación | Propósito y vistas destacadas |
| --- | --- |
| rfid | Lector de etiquetas RFID con endpoints de escaneo, reinicio y pruebas |
| ocpp | Panel OCPP 1.6 con vistas de estado y registros de cargadores |
| refs | Listado reciente de referencias y generador de QR al vuelo |
| awg | Calculadora de calibre AWG y referencias de ocupación de tubería |
| msg | Remitente de mensajes con notificaciones en dispositivo |

## Enlaces Externos
- [Python 3.12](https://www.python.org/downloads/release/python-31210/)
- [Licencia MIT](LICENSE)
- [Django](https://www.djangoproject.com/)
- [Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [Bootstrap](https://getbootstrap.com/)
