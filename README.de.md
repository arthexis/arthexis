# Arthexis-Konstellation

[![Testabdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![OCPP 1.6-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)


## Zweck

Die Arthexis-Konstellation ist eine [narrativ orientierte](https://de.wikipedia.org/wiki/Narration) auf [Django](https://www.djangoproject.com/) basierende [Softwaresuite](https://de.wikipedia.org/wiki/Softwarepaket), die Werkzeuge zur Verwaltung der [Ladeinfrastruktur für Elektrofahrzeuge](https://de.wikipedia.org/wiki/Lades%C3%A4ule) sowie zur Orchestrierung von [energiebezogenen Produkten](https://de.wikipedia.org/wiki/Produkt) und [Dienstleistungen](https://de.wikipedia.org/wiki/Dienstleistung) zentralisiert.

## Aktuelle Funktionen

- Kompatibel mit dem [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) als zentrales System und unterstützt:
  - Lebenszyklus und Sitzungen
    - `BootNotification`
    - `Heartbeat`
    - `StatusNotification`
    - `StartTransaction`
    - `StopTransaction`
  - Zugriff und Messung
    - `Authorize`
    - `MeterValues`
  - Wartung und Firmware
    - `DiagnosticsStatusNotification`
    - `FirmwareStatusNotification`
- [API](https://de.wikipedia.org/wiki/Programmierschnittstelle)-Integration mit [Odoo](https://www.odoo.com/), um:
  - Mitarbeiterzugänge über `res.users` zu synchronisieren
  - Den Produktkatalog über `product.product` abzufragen
- Läuft auf [Windows 11](https://www.microsoft.com/windows/windows-11) und [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Getestet für den [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Rollenarchitektur

Die Arthexis-Konstellation wird in vier Node-Rollen ausgeliefert, die auf unterschiedliche Einsatzszenarien zugeschnitten sind.

<table border="1" cellpadding="8" cellspacing="0">
  <thead>
    <tr>
      <th align="left">Rolle</th>
      <th align="left">Beschreibung &amp; gemeinsame Funktionen</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top"><strong>Terminal</strong></td>
      <td valign="top"><strong>Einzelanwender-Forschung und -Entwicklung</strong><br />Funktionen: GUI Toast</td>
    </tr>
    <tr>
      <td valign="top"><strong>Control</strong></td>
      <td valign="top"><strong>Tests einzelner Geräte und Spezialgeräte</strong><br />Funktionen: AP Public Wi-Fi, Celery Queue, GUI Toast, LCD Screen, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Satellite</strong></td>
      <td valign="top"><strong>Edge-Betrieb mit mehreren Geräten, Netzwerk und Datenerfassung</strong><br />Funktionen: AP Router, Celery Queue, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Constellation</strong></td>
      <td valign="top"><strong>Cloud-Orchestrierung für mehrere Nutzer</strong><br />Funktionen: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Kurzanleitung

### 1. Klonen
- **[Linux](https://de.wikipedia.org/wiki/Linux)**: Öffne ein [Terminal](https://de.wikipedia.org/wiki/Kommandozeile) und führe `git clone https://github.com/arthexis/arthexis.git` aus.
- **[Windows](https://de.wikipedia.org/wiki/Microsoft_Windows)**: Öffne [PowerShell](https://learn.microsoft.com/powershell/) oder [Git Bash](https://gitforwindows.org/) und führe denselben Befehl aus.

### 2. Starten und Stoppen
Terminal-Knoten können direkt mit den untenstehenden Skripten ohne Installation gestartet werden; die Rollen Control, Satellite und Constellation müssen vorher installiert werden. Beide Ansätze lauschen standardmäßig auf [`http://localhost:8000/`](http://localhost:8000/).

- **[VS Code](https://code.visualstudio.com/)**
   - Ordner öffnen und zum Bereich **Run and Debug** (`Ctrl+Shift+D`) wechseln.
   - Die Konfiguration **Run Server** (oder **Debug Server**) auswählen.
   - Auf den grünen Startknopf klicken. Den Server mit dem roten Quadrat (`Shift+F5`) anhalten.

- **[Shell](https://de.wikipedia.org/wiki/Shell_(Informatik))**
   - Linux: [`./start.sh`](start.sh) ausführen und mit [`./stop.sh`](stop.sh) anhalten.
   - Windows: [`start.bat`](start.bat) ausführen und mit `Ctrl+C` beenden.

### 3. Installieren und Aktualisieren
- **Linux:**
   - [`./install.sh`](install.sh) mit einem Flag für die Node-Rolle ausführen:
     - `--terminal` – Standard, wenn nicht angegeben, und empfohlen, wenn du unsicher bist. Terminal-Knoten können auch ohne Installation über die obigen Skripte gestartet/gestoppt werden.
     - `--control` – Bereitet das Einzelgerätetest-System vor.
     - `--satellite` – Konfiguriert den Edge-Knoten zur Datenerfassung.
     - `--constellation` – Aktiviert den Multiuser-Orchestrierungsstack.
   - `./install.sh --help` zeigt alle verfügbaren Optionen, falls du die Konfiguration über die Rollenvorgaben hinaus anpassen möchtest.
   - Aktualisieren mit [`./upgrade.sh`](upgrade.sh).

- **Windows:**
   - [`install.bat`](install.bat) zur Installation (Terminal-Rolle) und [`upgrade.bat`](upgrade.bat) zum Aktualisieren ausführen.
   - Für den Start im Terminalmodus (Standard) ist keine Installation erforderlich.

### 4. Administration
[`http://localhost:8000/admin/`](http://localhost:8000/admin/) für den [Django-Admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) und [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) für die [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/) aufrufen. Verwende `--port` mit den Startskripten oder dem Installer, wenn ein anderer Port benötigt wird.

## Sigils

Sigils sind Platzhalter in eckigen Klammern wie `[ENV.SMTP_PASSWORD]`, die Arthexis zur Laufzeit auflöst. Sie ermöglichen es, Konfigurationsgeheimnisse, Systemmetadaten oder Datensätze aus anderen Apps einzubinden, ohne Werte zu duplizieren.

### Syntax im Überblick

- `[PREFIX.KEY]` &mdash; gibt ein Feld oder Attribut zurück. Bindestriche sowie Groß- und Kleinschreibung werden automatisch normalisiert.
- `[PREFIX=IDENTIFIER.FELD]` &mdash; wählt einen bestimmten Datensatz über Primärschlüssel oder ein anderes eindeutiges Feld aus.
- `[PREFIX:FELD=WERT.ATTRIBUT]` &mdash; filtert über ein benutzerdefiniertes Feld anstelle des Primärschlüssels.
- `[PREFIX.FELD=[ANDERER.SIGIL]]` &mdash; verschachtelt Sigils; der Wert nach `=` wird vor dem äußeren Token aufgelöst.
- `[PREFIX]` &mdash; bei Entitätspräfixen liefert dies das serialisierte Objekt als JSON; bei Konfigurationspräfixen ergibt es eine leere Zeichenfolge, wenn der Schlüssel fehlt.

Die Plattform bringt drei Konfigurationspräfixe mit:

- `ENV` liest Umgebungsvariablen.
- `CONF` liest Django-Einstellungen.
- `SYS` stellt berechnete Systeminformationen wie Build-Metadaten bereit.

Weitere Präfixe werden über **Sigil Roots** definiert, die einen Kurznamen (z. B. `ROLE`, `ODOO` oder `USER`) einem Django-Modell zuordnen. In **Admin &rarr; Sigil Builder** (`/admin/sigil-builder/`) kannst du sie einsehen und die Auflösung testen.

Unbekannte Präfixe bleiben unverändert (z. B. `[UNKNOWN.VALUE]`) und werden protokolliert. Ist das optionale `gway`-CLI installiert, versucht der Resolver, nicht aufgelöste Tokens dorthin weiterzuleiten, bevor der Originaltext zurückgegeben wird.

## Support

Kontakt per [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) oder besuche unsere [Webseite](https://www.gelectriic.com/) für [professionelle Dienstleistungen](https://de.wikipedia.org/wiki/Dienstleistung) und [kommerziellen Support](https://de.wikipedia.org/wiki/Technischer_Support).

## Projektleitlinien

- [AGENTS](AGENTS.md) – Betriebsleitfaden für Repository-Workflows, Tests und Release-Management.
- [DESIGN](DESIGN.md) – Gestaltungs-, UX- und Branding-Richtlinien, die für alle Oberflächen gelten.

## Über mich

> "Wie bitte, du willst auch etwas über mich wissen? Nun, ich mag es, [Software zu entwickeln](https://de.wikipedia.org/wiki/Softwareentwicklung), [Pen-&-Paper-Rollenspiele](https://de.wikipedia.org/wiki/Rollenspiel), lange Spaziergänge am [Strand](https://de.wikipedia.org/wiki/Strand) und eine vierte geheime Sache."
> --Arthexis
