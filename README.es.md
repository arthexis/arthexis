# Constelación Arthexis

## Propósito
Arthexis Constellation es una suite basada en Django que centraliza herramientas para gestionar infraestructura de carga y servicios relacionados.

## Instalación
1. Clonar el repositorio: `git clone <repository_url>`
2. Entrar en el directorio del proyecto: `cd arthexis`
3. *(Opcional)* Crear y activar un entorno virtual: `python -m venv .venv` y `source .venv/bin/activate`
4. Instalar dependencias: `pip install -r requirements.txt`

## Configuración
1. `python manage.py migrate`
2. `python manage.py runserver`
3. Ejecutar comandos de administración con `python manage.py <command>`

## Aplicaciones Incluidas
| Aplicación | Propósito |
| --- | --- |
| accounts | Cuentas de usuario, inicio de sesión con RFID y gestión de crédito |
| app | Utilidades básicas y ajustes del modelo del sitio |
| awg | Referencias y calculadora de calibre de alambre americano |
| emails | Plantillas de correo electrónico y mensajería |
| integrations | Integraciones con servicios externos como Bluesky, Facebook y Odoo |
| nodes | Registrar nodos del proyecto y administrar plantillas de NGINX |
| ocpp | Gestión de puntos de carga OCPP 1.6 |
| references | Referencias reutilizables y códigos QR |
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

---

*Nota: no modifiques este README salvo que se indique. Usa los admindocs de Django para la documentación de las aplicaciones.*

