# Constelación Arthexis

[![Cobertura](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml)

## Propósito

Constelación Arthexis es una [suite de software](https://es.wikipedia.org/wiki/Suite_de_software) [impulsada por la narrativa](https://es.wikipedia.org/wiki/Narrativa) basada en [Django](https://www.djangoproject.com/) que centraliza herramientas para gestionar la [infraestructura de carga de vehículos eléctricos](https://es.wikipedia.org/wiki/Punto_de_recarga) y orquestar [productos](https://es.wikipedia.org/wiki/Producto_(econom%C3%ADa)) y [servicios](https://es.wikipedia.org/wiki/Servicio_(econom%C3%ADa)) relacionados con la [energía](https://es.wikipedia.org/wiki/Energ%C3%ADa).

## Características actuales

- Compatible con el [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) como sistema central, gestionando:
  - Ciclo de vida y sesiones
    - `BootNotification`
    - `Heartbeat`
    - `StatusNotification`
    - `StartTransaction`
    - `StopTransaction`
  - Acceso y medición
    - `Authorize`
    - `MeterValues`
  - Mantenimiento y firmware
    - `DiagnosticsStatusNotification`
    - `FirmwareStatusNotification`
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
Los nodos Terminal pueden iniciarse directamente con los siguientes scripts sin instalar; los roles Control, Satélite y Constelación deben instalarse primero. Ambos métodos escuchan en [`http://localhost:8000/`](http://localhost:8000/) de forma predeterminada.

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

- **Windows:**
   - Ejecuta [`install.bat`](install.bat) para instalar (rol Terminal) y [`upgrade.bat`](upgrade.bat) para actualizar.
   - No es necesario instalar para iniciar en modo Terminal (el predeterminado).

### 4. Administración
Visita [`http://localhost:8000/admin/`](http://localhost:8000/admin/) para el [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) y [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) para la [documentación de administración](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Usa `--port` con los scripts de inicio o el instalador si necesitas exponer otro puerto.

## Soporte

Contáctenos en [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visite nuestro [sitio web](https://www.gelectriic.com/) para [servicios profesionales](https://es.wikipedia.org/wiki/Servicios_profesionales) y [soporte comercial](https://es.wikipedia.org/wiki/Soporte_t%C3%A9cnico).

## Sobre mí

> "¿Qué? ¿También quieres saber sobre mí? Bueno, disfruto el [desarrollo de software](https://es.wikipedia.org/wiki/Desarrollo_de_software), los [juegos de rol](https://es.wikipedia.org/wiki/Juego_de_rol), largas caminatas por la [playa](https://es.wikipedia.org/wiki/Playa) y una cuarta cosa secreta."
> --Arthexis

