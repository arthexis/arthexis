# Constelación Arthexis

[![Cobertura](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![Cobertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)


## Propósito

Constelación Arthexis es una [suite de software](https://es.wikipedia.org/wiki/Suite_de_software) [impulsada por la narrativa](https://es.wikipedia.org/wiki/Narrativa) basada en [Django](https://www.djangoproject.com/) que centraliza herramientas para gestionar la [infraestructura de carga de vehículos eléctricos](https://es.wikipedia.org/wiki/Punto_de_recarga) y orquestar [productos](https://es.wikipedia.org/wiki/Producto_(econom%C3%ADa)) y [servicios](https://es.wikipedia.org/wiki/Servicio_(econom%C3%ADa)) relacionados con la [energía](https://es.wikipedia.org/wiki/Energ%C3%ADa).

## Características actuales

- Compatible con el [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) como sistema central. Las acciones soportadas se resumen a continuación.

  **Punto de carga → CSMS**

  | Acción | Qué hacemos |
  | --- | --- |
  | `Authorize` | Validamos solicitudes de autorización por RFID o token antes de iniciar una sesión. |
  | `BootNotification` | Registramos el punto de carga y actualizamos identidad, firmware y estado. |
  | `DataTransfer` | Aceptamos cargas útiles específicas del proveedor y registramos los resultados. |
  | `DiagnosticsStatusNotification` | Seguimos el progreso de las cargas de diagnósticos iniciadas desde la oficina central. |
  | `FirmwareStatusNotification` | Seguimos los eventos del ciclo de vida de actualizaciones de firmware desde los puntos de carga. |
  | `Heartbeat` | Mantenemos viva la sesión websocket y actualizamos la última conexión. |
  | `MeterValues` | Guardamos lecturas periódicas de energía y potencia mientras la transacción está activa. |
  | `StartTransaction` | Creamos sesiones de carga con valores iniciales y datos de identificación. |
  | `StatusNotification` | Reflejamos la disponibilidad y los fallos del conector en tiempo real. |
  | `StopTransaction` | Cerramos sesiones de carga, capturando lecturas finales y motivos de cierre. |

  **CSMS → Punto de carga**

  | Acción | Qué hacemos |
  | --- | --- |
  | `CancelReservation` | Cancelamos reservas pendientes y liberamos conectores directamente desde el centro de control. |
  | `ChangeAvailability` | Cambiamos conectores o estaciones completas entre estados operativos e inoperativos. |
  | `DataTransfer` | Enviamos comandos específicos del proveedor y registramos la respuesta. |
  | `GetConfiguration` | Consultamos al dispositivo por los valores actuales de las claves de configuración seguidas. |
  | `GetLocalListVersion` | Obtenemos la versión actual de la lista blanca de RFID y sincronizamos las entradas reportadas por el punto de carga. |
  | `RemoteStartTransaction` | Iniciamos sesiones de carga de forma remota para un cliente o token identificado. |
  | `RemoteStopTransaction` | Terminamos sesiones activas desde el centro de control. |
  | `ReserveNow` | Reservamos conectores para sesiones futuras con asignación automática y seguimiento de confirmaciones. |
  | `Reset` | Solicitamos un reinicio suave o completo para recuperarnos de fallos. |
  | `SendLocalList` | Publicamos los RFID liberados y aprobados como la lista de autorización local del punto de carga. |
  | `TriggerMessage` | Pedimos al dispositivo un mensaje inmediato (por ejemplo estado o diagnósticos). |
  | `UpdateFirmware` | Entregamos paquetes de firmware a los cargadores con tokens de descarga seguros y seguimos las respuestas de instalación. |

  **Hoja de ruta OCPP 1.6.** Las siguientes acciones del catálogo están en nuestra lista de tareas: `ChangeConfiguration`, `ClearCache`, `ClearChargingProfile`, `GetCompositeSchedule`, `GetDiagnostics`, `SetChargingProfile`, `UnlockConnector`.

- Reservas de puntos de carga con asignación automática de conectores, vinculación a cuentas de energía y RFIDs, confirmación del EVCS y cancelación desde el centro de control.
- Integración de [API](https://es.wikipedia.org/wiki/Interfaz_de_programaci%C3%B3n_de_aplicaciones) con [Odoo](https://www.odoo.com/) para:
  - Sincronizar credenciales de empleados mediante `res.users`
  - Consultar el catálogo de productos mediante `product.product`
- Funciona en [Windows 11](https://www.microsoft.com/es-es/windows/windows-11) y [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Probado para la [Raspberry Pi 4 Modelo B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Arquitectura de roles

Constelación Arthexis se distribuye en cuatro roles de nodo que adaptan la plataforma a distintos escenarios de despliegue.

<table border="1" cellpadding="8" cellspacing="0">
  <thead>
    <tr>
      <th align="left">Rol</th>
      <th align="left">Descripción y funciones comunes</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top"><strong>Terminal</strong></td>
      <td valign="top"><strong>Investigación y desarrollo de un solo usuario</strong><br />Funciones: GUI Toast</td>
    </tr>
    <tr>
      <td valign="top"><strong>Control</strong></td>
      <td valign="top"><strong>Pruebas de un solo dispositivo y equipos para tareas especiales</strong><br />Funciones: AP Public Wi-Fi, Celery Queue, GUI Toast, LCD Screen, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Satélite</strong></td>
      <td valign="top"><strong>Periferia multidispositivo, redes y adquisición de datos</strong><br />Funciones: AP Router, Celery Queue, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Constelación</strong></td>
      <td valign="top"><strong>Nube multiusuario y orquestación</strong><br />Funciones: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Quick Guide

### 1. Clonar
- **[Linux](https://es.wikipedia.org/wiki/Linux)**: abre una [terminal](https://es.wikipedia.org/wiki/Interfaz_de_l%C3%ADnea_de_comandos) y ejecuta `git clone https://github.com/arthexis/arthexis.git`.
- **[Windows](https://es.wikipedia.org/wiki/Microsoft_Windows)**: abre [PowerShell](https://learn.microsoft.com/es-es/powershell/) o [Git Bash](https://gitforwindows.org/) y ejecuta el mismo comando.

### 2. Iniciar y detener
Los nodos Terminal pueden iniciarse directamente con los siguientes scripts sin instalar; los roles Control, Satélite y Constelación deben instalarse primero. Ambos métodos escuchan en [`http://localhost:8888/`](http://localhost:8888/) de forma predeterminada.

- **[VS Code](https://code.visualstudio.com/)**
   - Abre la carpeta y ve al panel **Run and Debug** (`Ctrl+Shift+D`).
   - Selecciona la configuración **Run Server** (o **Debug Server**).
   - Presiona el botón verde de inicio. Detén el servidor con el cuadrado rojo (`Shift+F5`).

- **[Shell](https://es.wikipedia.org/wiki/Shell_de_unidad_de_comandos)**
   - Linux: ejecuta [`./start.sh`](start.sh) y detén con [`./stop.sh`](stop.sh).
   - Windows: ejecuta [`start.bat`](start.bat) y detén con `Ctrl+C`.

### 3. Instalar y actualizar
- **Linux:**
   - Ejecuta [`./install.sh`](install.sh) con un flag de rol de nodo:
     - `--terminal`: rol predeterminado si no se especifica y recomendado si no sabes cuál elegir. Los nodos Terminal también pueden usar los scripts anteriores para iniciar/detener sin instalar.
     - `--control`: prepara el equipo de control para pruebas de un solo dispositivo.
     - `--satellite`: configura el nodo perimetral de adquisición de datos.
     - `--constellation`: habilita la pila de orquestación multiusuario.
   - Usa `./install.sh --help` para ver la lista completa de flags si necesitas personalizar el nodo más allá del rol.
   - Actualiza con [`./upgrade.sh`](upgrade.sh).
   - Consulta el [Manual de scripts de instalación y ciclo de vida](docs/development/install-lifecycle-scripts-manual.md) para ver la descripción completa de los flags y las notas operativas.
   - Revisa la [Guía de actualización](docs/UPGRADE.md) para los pasos manuales requeridos cuando ciertas migraciones dejan de automatizarse.

- **Windows:**
   - Ejecuta [`install.bat`](install.bat) para instalar (rol Terminal) y [`upgrade.bat`](upgrade.bat) para actualizar.
   - No es necesario instalar para iniciar en modo Terminal (el predeterminado).

### 4. Administración
Visita [`http://localhost:8888/admin/`](http://localhost:8888/admin/) para el [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) y [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) para la [documentación de administración](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Usa `--port` con los scripts de inicio o el instalador si necesitas exponer otro puerto.

## Sigilos

Los sigilos son tokens entre corchetes como `[ENV.SMTP_PASSWORD]` que Arthexis expande en tiempo de ejecución. Permiten referenciar secretos de configuración, metadatos del sistema o registros de otras aplicaciones sin duplicar valores en el proyecto.

### Sintaxis resumida

- `[PREFIX.KEY]` &mdash; devuelve un campo o atributo. Los guiones y las mayúsculas/minúsculas se normalizan automáticamente.
- `[PREFIX=IDENTIFICADOR.CAMPO]` &mdash; selecciona un registro concreto por clave primaria o por cualquier campo único.
- `[PREFIX:CAMPO=VALOR.ATRIBUTO]` &mdash; filtra por un campo personalizado en lugar de la clave primaria.
- `[PREFIX.CAMPO=[OTRO.SIGILO]]` &mdash; permite anidar sigilos; el valor después de `=` se resuelve antes que el token externo.
- `[PREFIX]` &mdash; para prefijos de entidad devuelve el objeto serializado en JSON; para prefijos de configuración se resuelve en una cadena vacía si falta la clave.

La plataforma incluye tres prefijos de configuración:

- `ENV` lee variables de entorno.
- `CONF` lee ajustes de Django.
- `SYS` expone información del sistema calculada, como metadatos de compilación.

Los prefijos adicionales se definen mediante **Sigil Roots**, que vinculan un código corto (por ejemplo `ROLE`, `ODOO` o `USER`) con un modelo de Django. Puedes revisarlos desde **Admin &rarr; Sigil Builder** (`/admin/sigil-builder/`), que también ofrece una consola de pruebas.

Los prefijos desconocidos permanecen sin cambios (por ejemplo `[UNKNOWN.VALUE]`) y se registran en los logs.

## Soporte

Contáctenos en [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visite nuestro [sitio web](https://www.gelectriic.com/) para [servicios profesionales](https://es.wikipedia.org/wiki/Servicios_profesionales) y [soporte comercial](https://es.wikipedia.org/wiki/Soporte_t%C3%A9cnico).

## Directrices del proyecto

- [AGENTS](AGENTS.md) – manual operativo para los flujos de trabajo del repositorio, las pruebas y la gestión de versiones.
- [DESIGN](DESIGN.md) – guía de diseño visual, experiencia de usuario y branding que deben seguir todas las interfaces.

## Sobre mí

> "¿Qué? ¿También quieres saber sobre mí? Bueno, disfruto el [desarrollo de software](https://es.wikipedia.org/wiki/Desarrollo_de_software), los [juegos de rol](https://es.wikipedia.org/wiki/Juego_de_rol), largas caminatas por la [playa](https://es.wikipedia.org/wiki/Playa) y una cuarta cosa secreta."
> --Arthexis

