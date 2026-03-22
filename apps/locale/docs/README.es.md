# Constellation

[![Cobertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Cobertura OCPP 2.0.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Cobertura OCPP 2.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)
[![Install CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/install-hourly.yml?branch=main&label=Install%20CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/install-hourly.yml) [![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/) [![Licencia: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://github.com/arthexis/arthexis/blob/main/LICENSE)


## Propósito

Constelación Arthexis es una suite de software basada en Django que centraliza herramientas para gestionar la infraestructura de carga de vehículos eléctricos y orquestar productos y servicios relacionados con la energía.

Visita el [Informe de cambios](https://arthexis.com/changelog/) para explorar las funciones pasadas y futuras junto con otras actualizaciones.

## Características de la suite

- Compatible con el [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) por defecto, permitiendo que los puntos de carga se actualicen a protocolos más nuevos si los admiten.

  **Punto de carga → CSMS**

  | Acción | 1.6 | 2.0.1 | 2.1 | Qué hacemos |
  | --- | --- | --- | --- | --- |
  | `Authorize` | ✅ | ✅ | ✅ | Validamos solicitudes de autorización por RFID o token antes de iniciar una sesión. |
  | `BootNotification` | ✅ | ✅ | ✅ | Registramos el punto de carga y actualizamos identidad, firmware y estado. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Aceptamos cargas útiles específicas del proveedor y registramos los resultados. |
  | `DiagnosticsStatusNotification` | ✅ | — | — | Seguimos el progreso de las cargas de diagnósticos iniciadas desde la oficina central. |
  | `FirmwareStatusNotification` | ✅ | ✅ | ✅ | Seguimos los eventos del ciclo de vida de actualizaciones de firmware desde los puntos de carga. |
  | `Heartbeat` | ✅ | ✅ | ✅ | Mantenemos viva la sesión websocket y actualizamos la última conexión. |
  | `LogStatusNotification` | — | ✅ | ✅ | Informamos el progreso de las cargas de registros del punto de carga para supervisar diagnósticos. |
  | `MeterValues` | ✅ | ✅ | ✅ | Guardamos lecturas periódicas de energía y potencia mientras la transacción está activa. |
  | `SecurityEventNotification` | — | ✅ | ✅ | Registramos eventos de seguridad reportados por los puntos de carga para auditoría. |
  | `StartTransaction` | ✅ | — | — | Creamos sesiones de carga con valores iniciales y datos de identificación. |
  | `StatusNotification` | ✅ | ✅ | ✅ | Reflejamos la disponibilidad y los fallos del conector en tiempo real. |
  | `StopTransaction` | ✅ | — | — | Cerramos sesiones de carga, capturando lecturas finales y motivos de cierre. |

  **CSMS → Punto de carga**

  | Acción | 1.6 | 2.0.1 | 2.1 | Qué hacemos |
  | --- | --- | --- | --- | --- |
  | `CancelReservation` | ✅ | ✅ | ✅ | Cancelamos reservas pendientes y liberamos conectores directamente desde el centro de control. |
  | `ChangeAvailability` | ✅ | ✅ | ✅ | Cambiamos conectores o estaciones completas entre estados operativos e inoperativos. |
  | `ChangeConfiguration` | ✅ | — | — | Actualizamos los ajustes compatibles del cargador y registramos los valores aplicados en el centro de control. |
  | `ClearCache` | ✅ | ✅ | ✅ | Limpiamos la caché de autorizaciones local para forzar consultas frescas al CSMS. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Enviamos comandos específicos del proveedor y registramos la respuesta. |
  | `GetConfiguration` | ✅ | — | — | Consultamos al dispositivo por los valores actuales de las claves de configuración seguidas. |
  | `GetDiagnostics` | ✅ | — | — | Solicitamos una carga de diagnóstico del dispositivo hacia una URL firmada para resolver problemas. |
  | `GetLocalListVersion` | ✅ | ✅ | ✅ | Obtenemos la versión actual de la lista blanca de RFID y sincronizamos las entradas reportadas por el punto de carga. |
  | `RemoteStartTransaction` | ✅ | — | — | Iniciamos sesiones de carga de forma remota para un cliente o token identificado. |
  | `RemoteStopTransaction` | ✅ | — | — | Terminamos sesiones activas desde el centro de control. |
  | `ReserveNow` | ✅ | ✅ | ✅ | Reservamos conectores para sesiones futuras con asignación automática y seguimiento de confirmaciones. |
  | `Reset` | ✅ | ✅ | ✅ | Solicitamos un reinicio suave o completo para recuperarnos de fallos. |
  | `SendLocalList` | ✅ | ✅ | ✅ | Publicamos los RFID liberados y aprobados como la lista de autorización local del punto de carga. |
  | `TriggerMessage` | ✅ | ✅ | ✅ | Pedimos al dispositivo un mensaje inmediato (por ejemplo estado o diagnósticos). |
  | `UnlockConnector` | ✅ | ✅ | ✅ | Liberamos conectores bloqueados sin intervención en sitio. |
  | `UpdateFirmware` | ✅ | ✅ | ✅ | Entregamos paquetes de firmware a los cargadores con tokens de descarga seguros y seguimos las respuestas de instalación. |

- Reservas de puntos de carga con asignación automática de conectores, vinculación a cuentas de energía y RFIDs, confirmación del EVCS y cancelación desde el centro de control.
- Consulta el [cookbook de integración de API con Odoo](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/odoo-integrations.md) para ver cómo sincronizamos credenciales de empleados mediante `res.users` y consultas al catálogo de productos mediante `product.product`.
- Funciona en Windows 11 y Ubuntu 24.
- Probado para la Raspberry Pi 4 Modelo B.

Proyecto en desarrollo abierto y muy activo.

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
      <td valign="top"><strong>Watchtower</strong></td>
      <td valign="top"><strong>Nube multiusuario y orquestación</strong><br />Funciones: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Quick Guide

### 1. Clonar
- **Linux**: abre una terminal y ejecuta `git clone https://github.com/arthexis/arthexis.git`.
- **Windows**: abre PowerShell o Git Bash y ejecuta el mismo comando.

### 2. Iniciar y detener
Los nodos Terminal pueden iniciarse directamente con los siguientes scripts sin instalar; los roles Control, Satélite y Watchtower deben instalarse primero. Ambos métodos escuchan en `localhost:8888/` de forma predeterminada.

- **VS Code**
   - Abre la carpeta y ve al panel **Run and Debug** (`Ctrl+Shift+D`).
   - Selecciona la configuración **Run Server** (o **Debug Server**).
   - Presiona el botón verde de inicio. Detén el servidor con el cuadrado rojo (`Shift+F5`).

- **Shell**
   - Linux: ejecuta [`./start.sh`](https://github.com/arthexis/arthexis/blob/main/start.sh) y detén con [`./stop.sh`](https://github.com/arthexis/arthexis/blob/main/stop.sh).
   - Windows: ejecuta [`start.bat`](https://github.com/arthexis/arthexis/blob/main/start.bat) y detén con `Ctrl+C`.

### 3. Instalar y actualizar
- **Linux:**
   - Ejecuta [`./install.sh`](https://github.com/arthexis/arthexis/blob/main/install.sh) con un flag de rol de nodo; consulta la tabla de Arquitectura de roles anterior para ver las opciones y predeterminados de cada rol.
   - Usa `./install.sh --help` para ver la lista completa de flags si necesitas personalizar el nodo más allá del rol.
   - Actualiza con [`./upgrade.sh`](https://github.com/arthexis/arthexis/blob/main/upgrade.sh).
   - Consulta el [Manual de scripts de instalación y ciclo de vida](https://github.com/arthexis/arthexis/blob/main/docs/development/install-lifecycle-scripts-manual.md) para ver la descripción completa de los flags y las notas operativas.
   - Revisa el [Flujo de autoactualización](https://github.com/arthexis/arthexis/blob/main/docs/auto-upgrade.md) para conocer cómo se ejecutan las actualizaciones delegadas y cómo monitorearlas.

- **Windows:**
   - Ejecuta [`install.bat`](https://github.com/arthexis/arthexis/blob/main/install.bat) para instalar (rol Terminal) y [`upgrade.bat`](https://github.com/arthexis/arthexis/blob/main/upgrade.bat) para actualizar.
   - No es necesario instalar para iniciar en modo Terminal (el predeterminado).

### 4. Administración
- Accede al Django admin en `localhost:8888/admin/` para revisar y gestionar datos en vivo. Usa `--port` con los scripts de inicio o el instalador si necesitas exponer otro puerto.
- Consulta la documentación de administración en `localhost:8888/admindocs/` para leer la API generada automáticamente a partir de tus modelos.
- Esquema de canales de actualización:

| Canal | Cadencia de revisión | Propósito | Flag de activación |
| --- | --- | --- | --- |
| Estable | Semanal (jueves antes de las 5:00 AM) | Sigue revisiones de lanzamientos con verificaciones automáticas semanales. | `--stable` |
| Latest | Diario (a la misma hora) | Sigue las revisiones más recientes de la línea principal con verificaciones diarias. | `--latest` / `-l` o `--unstable` |
| Manual | Ninguna (solo actualizaciones manuales) | Desactiva el bucle de actualización automática para control total del operador. Este es el comportamiento predeterminado si no se indica canal. | _Ejecuta upgrades bajo demanda sin indicar un canal._ |
- Sigue la [Guía de instalación y administración](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/install-start-stop-upgrade-uninstall.md) para tareas de despliegue, ciclo de vida y runbooks operativos.
- Integra y da mantenimiento a los cargadores con el [Cookbook de conectividad y mantenimiento EVCS](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/evcs-connectivity-maintenance.md).
- Configura pasarelas de pago con el [Cookbook de procesadores de pago](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/payment-processors.md).
- Revisa el [Cookbook de sigilos](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/sigils.md) cuando configures ajustes tokenizados entre entornos.
- Entiende los fixtures semilla y los archivos por usuario con [Gestión de datos locales del nodo](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/managing-local-node-data.md).
- Gestiona exportaciones, importaciones y trazabilidad con el [Cookbook de datos de usuario](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/user-data.md).
- Planifica estrategias de despliegue de funciones con el [Cookbook de características de nodos](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/node-features.md).
- Organiza accesos directos para usuarios avanzados mediante el [Cookbook de favoritos](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/favorites.md).
- Conecta espacios de trabajo de Slack con el [Cookbook de incorporación de Slack Bot](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/slack-bot-onboarding.md).

### 5. Desarrollo
- Revisa la [Biblioteca de documentación para desarrolladores](../../../docs/index.md) para referencias de arquitectura, manuales de protocolo y flujos de contribución.
- Nota: este enlace apunta a la documentación dentro del repositorio, no a una ruta web en tiempo de ejecución.

## Soporte

Constelación Arthexis sigue en desarrollo muy activo y se agregan nuevas funciones cada día.

Si decides usar nuestra suite para tus proyectos de energía, puedes contactarnos en [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visitar nuestro [sitio web](https://www.gelectriic.com/) para servicios profesionales y soporte comercial.

## Sobre mí

> "¿Qué? ¿También quieres saber sobre mí? Bueno, disfruto el desarrollo de software, los juegos de rol, largas caminatas por la playa y una cuarta cosa secreta."
> --Arthexis

## Licencia y patrocinio

Arthexis se distribuye bajo la Arthexis Contribution Reciprocity License 1.0. Además del código, la documentación, las revisiones y el mantenimiento, también consideramos que patrocinar Arthexis y realizar trabajo remunerado o voluntario para las dependencias de código abierto en las que nos apoyamos es una contribución válida e importante.

Si Arthexis ayuda a tu equipo, revisa los términos de la licencia en [`LICENSE`](../../../LICENSE) y considera patrocinar o apoyar directamente a las personas mantenedoras de las bibliotecas, frameworks y proyectos de infraestructura que hacen posible esta suite. Apoyar esas dependencias ayuda a mantener sano todo el ecosistema de Arthexis.
