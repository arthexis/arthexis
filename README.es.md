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

## Aplicaciones Incluidas
| Aplicación | Propósito |
| --- | --- |
| accounts | Cuentas de usuario, inicio de sesión con RFID y gestión de crédito |
| app | Utilidades básicas y ajustes del modelo del sitio |
| awg | Referencias y calculadora de calibre de alambre americano |
| emails | Plantillas de correo electrónico y mensajería |
| integrator | Integraciones con servicios externos como Bluesky, Facebook y Odoo |
| nodes | Registrar nodos del proyecto y administrar plantillas de NGINX |
| ocpp | Gestión de puntos de carga OCPP 1.6 |
| refs | Referencias reutilizables y códigos QR |
| release | Herramientas de empaquetado y publicación en PyPI |
| rfid | Modelo de etiquetas RFID y utilidades |
| website | Sitio predeterminado y generador de README |

## Enlaces Externos
- [Python 3.12](https://www.python.org/downloads/release/python-31210/)
- [Licencia MIT](LICENSE)
- [Django](https://www.djangoproject.com/)
- [Channels](https://channels.readthedocs.io/)
- [Celery](https://docs.celeryq.dev/)
- [Bootstrap](https://getbootstrap.com/)
