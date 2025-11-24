# Arthexis-Konstellation

[![CI](https://github.com/arthexis/arthexis/actions/workflows/ci.yml/badge.svg)](https://github.com/arthexis/arthexis/actions/workflows/ci.yml) [![Testabdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![OCPP 1.6-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.0.1-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.1-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Lizenz: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)


## Zweck

Die Arthexis-Konstellation ist eine auf [Django](https://www.djangoproject.com/) basierende [Softwaresuite](https://de.wikipedia.org/wiki/Softwarepaket), die Werkzeuge zur Verwaltung der [Ladeinfrastruktur für Elektrofahrzeuge](https://de.wikipedia.org/wiki/Lades%C3%A4ule) sowie zur Orchestrierung von [energiebezogenen Produkten](https://de.wikipedia.org/wiki/Produkt) und [Dienstleistungen](https://de.wikipedia.org/wiki/Dienstleistung) zentralisiert.

Besuche den öffentlichen [Changelog-Bericht](https://arthexis.com/changelog/), um unveröffentlichte und historische Aktualisierungen direkt aus Git-Commits nachzuvollziehen.

Lies den aktuellen [Developer-Artikel](https://arthexis.com/articles/protoline-integration/) mit den wichtigsten Informationen zum kommenden Release und zum Rollout.

## Neueste Entwicklungen

- Die OCPP-1.6-CSMS-Abdeckung umfasst jetzt `GetDiagnostics`, `RemoteStartTransaction`, `RemoteStopTransaction` und `SetChargingProfile`, womit 96 % der Aufrufe unterstützt werden und neue Call-Result-/Error-Handler samt Admin-Protokollen zur Verfügung stehen.

## Aktuelle Funktionen

- Kompatibel mit dem [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) als zentrales System. Unterstützte Aktionen im Überblick.

  **Ladepunkt → CSMS**

  | Aktion | 1.6 | 2.0.1 | 2.1 | Was wir erledigen |
  | --- | --- | --- | --- | --- |
  | `Authorize` | ✅ | ✅ | ✅ | Validieren RFID- oder Token-Autorisierungsanfragen vor Sitzungsstart. |
  | `BootNotification` | ✅ | ✅ | ✅ | Registrieren den Ladepunkt und aktualisieren Identität, Firmware und Status. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Akzeptieren herstellerspezifische Nutzdaten und protokollieren die Ergebnisse. |
  | `DiagnosticsStatusNotification` | ✅ | — | — | Verfolgen den Fortschritt von aus dem Backoffice gestarteten Diagnoseuploads. |
  | `FirmwareStatusNotification` | ✅ | ✅ | ✅ | Verfolgen Firmware-Update-Lebenszyklusmeldungen der Ladepunkte. |
  | `Heartbeat` | ✅ | ✅ | ✅ | Halten die Websocket-Sitzung aktiv und aktualisieren den Zeitstempel der letzten Verbindung. |
  | `LogStatusNotification` | — | ✅ | ✅ | Verfolgen den Fortschritt von Log-Uploads vom Ladepunkt für die Diagnostiküberwachung. |
  | `MeterValues` | ✅ | ✅ | ✅ | Speichern periodische Energie- und Leistungswerte während aktiver Transaktionen. |
  | `SecurityEventNotification` | — | ✅ | ✅ | Erfassen Sicherheitsereignisse, die vom Ladepunkt gemeldet werden, für Prüfpfade. |
  | `StartTransaction` | ✅ | — | — | Erstellen Ladevorgänge mit Startzählerstand und Identifikationsdaten. |
  | `StatusNotification` | ✅ | ✅ | ✅ | Spiegeln Verfügbarkeits- und Fehlerzustände der Anschlüsse in Echtzeit. |
  | `StopTransaction` | ✅ | — | — | Schließen Ladevorgänge und erfassen Endzählerstand sowie Stopgrund. |

  **CSMS → Ladepunkt**

  | Aktion | 1.6 | 2.0.1 | 2.1 | Was wir erledigen |
  | --- | --- | --- | --- | --- |
  | `CancelReservation` | ✅ | ✅ | ✅ | Stornieren ausstehender Reservierungen und geben Anschlüsse direkt aus der Leitwarte frei. |
  | `ChangeAvailability` | ✅ | ✅ | ✅ | Schalten Anschlüsse oder die gesamte Station zwischen betriebsbereit und außer Betrieb. |
  | `ChangeConfiguration` | ✅ | — | — | Aktualisieren unterstützte Ladeeinstellungen und übernehmen angewendete Werte in der Leitwarte. |
  | `ClearCache` | ✅ | ✅ | ✅ | Leeren lokale Autorisierungscaches, um erneute Abgleiche über das CSMS zu erzwingen. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Senden herstellerspezifische Befehle und protokollieren die Antwort des Ladepunkts. |
  | `GetConfiguration` | ✅ | — | — | Fragen die aktuellen Werte der überwachten Konfigurationsschlüssel ab. |
  | `GetDiagnostics` | ✅ | — | — | Fordern ein Diagnosenarchiv an, das zu einer signierten URL hochgeladen wird, um Störungen zu prüfen. |
  | `GetLocalListVersion` | ✅ | ✅ | ✅ | Rufen die aktuelle RFID-Whitelist-Version ab und synchronisieren die vom Ladepunkt gemeldeten Einträge. |
  | `RemoteStartTransaction` | ✅ | — | — | Starten Ladevorgänge remote für identifizierte Kundinnen und Kunden oder Tokens. |
  | `RemoteStopTransaction` | ✅ | — | — | Beenden aktive Ladevorgänge aus der Leitwarte. |
  | `ReserveNow` | ✅ | ✅ | ✅ | Reservieren Anschlüsse für kommende Sitzungen mit automatischer Zuweisung und Bestätigungsnachverfolgung. |
  | `Reset` | ✅ | ✅ | ✅ | Fordern einen Soft- oder Hard-Reset zur Fehlerbehebung an. |
  | `SendLocalList` | ✅ | ✅ | ✅ | Veröffentlichen freigegebene und genehmigte RFIDs als lokale Autorisierungsliste des Ladepunkts. |
  | `TriggerMessage` | ✅ | ✅ | ✅ | Fordern sofortige Nachrichten an (z. B. Status oder Diagnose). |
  | `UnlockConnector` | ✅ | ✅ | ✅ | Entriegeln blockierte Anschlüsse ohne Vor-Ort-Einsatz. |
  | `UpdateFirmware` | ✅ | ✅ | ✅ | Liefern Firmwarepakete an Ladepunkte mit sicheren Download-Tokens und verfolgen Installationsrückmeldungen. |

  **OCPP-Roadmap.** Die geplante Arbeit für die OCPP-1.6-, 2.0.1- und 2.1-Kataloge findest du im [OCPP-Roadmap-Cookbook](docs/cookbooks/ocpp-roadmap.md).

- Ladepunktreservierungen mit automatischer Anschlusswahl, Verknüpfung zu Energiekonten und RFID-Tags, EVCS-Bestätigung sowie Stornierung über die Leitwarte.

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
      <td valign="top"><strong>Watchtower</strong></td>
      <td valign="top"><strong>Cloud-Orchestrierung für mehrere Nutzer</strong><br />Funktionen: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Kurzanleitung

### 1. Klonen
- **[Linux](https://de.wikipedia.org/wiki/Linux)**: Öffne ein [Terminal](https://de.wikipedia.org/wiki/Kommandozeile) und führe `git clone https://github.com/arthexis/arthexis.git` aus.
- **[Windows](https://de.wikipedia.org/wiki/Microsoft_Windows)**: Öffne [PowerShell](https://learn.microsoft.com/powershell/) oder [Git Bash](https://gitforwindows.org/) und führe denselben Befehl aus.

### 2. Starten und Stoppen
Terminal-Knoten können direkt mit den untenstehenden Skripten ohne Installation gestartet werden; die Rollen Control, Satellite und Watchtower müssen vorher installiert werden. Beide Ansätze lauschen standardmäßig auf [`http://localhost:8888/`](http://localhost:8888/).

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
     - `--watchtower` – Aktiviert den Multiuser-Orchestrierungsstack.
   - `./install.sh --help` zeigt alle verfügbaren Optionen, falls du die Konfiguration über die Rollenvorgaben hinaus anpassen möchtest.
   - Aktualisieren mit [`./upgrade.sh`](upgrade.sh).
   - Lies das [Manual zu Installations- & Lifecycle-Skripten](docs/development/install-lifecycle-scripts-manual.md) für vollständige Flag-Beschreibungen und Betriebsdetails.
   - Sieh dir den [Auto-Upgrade-Flow](docs/auto-upgrade.md) an, um zu verstehen, wie delegierte Upgrades laufen und wie du sie überwachst.

- **Windows:**
   - [`install.bat`](install.bat) zur Installation (Terminal-Rolle) und [`upgrade.bat`](upgrade.bat) zum Aktualisieren ausführen.
   - Für den Start im Terminalmodus (Standard) ist keine Installation erforderlich.

### 4. Administration
- Greife über [`http://localhost:8888/admin/`](http://localhost:8888/admin/) auf den [Django-Admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) zu, um Live-Daten zu prüfen und zu pflegen. Verwende `--port` mit den Startskripten oder dem Installer, wenn du einen anderen Port freigeben musst.
- Durchstöbere die [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/) unter [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/), um automatisch generierte API-Dokumentation deiner Modelle zu lesen.
- Upgrade-Kanäle: Neue Installationen setzen standardmäßig `--fixed` und lassen Auto-Upgrade deaktiviert. Aktiviere automatische Updates auf dem stabilen Kanal mit `--stable` (24-Stunden-Prüfungen gemäß den Releases) oder verfolge Hauptzweig-Revisionen mit `--unstable`/`--latest` (Prüfungen alle 15 Minuten).
- Folge dem [Installations- und Administrationshandbuch](docs/cookbooks/install-start-stop-upgrade-uninstall.md) für Deployment, Lifecycle-Aufgaben und operative Runbooks.
- Nimm Ladepunkte mit dem [EVCS-Konnektivitäts- und Wartungs-Cookbook](docs/cookbooks/evcs-connectivity-maintenance.md) in Betrieb und halte sie instand.
- Konfiguriere Zahlungs-Gateways mit dem [Payment Processors Cookbook](docs/cookbooks/payment-processors.md).
- Nutze das [Sigil-Cookbook](docs/cookbooks/sigils.md), wenn du tokenbasierte Einstellungen über Umgebungen hinweg konfigurierst.
- Verwalte Exporte, Importe und Prüfprotokolle mit dem [User-Data-Cookbook](docs/cookbooks/user-data.md).
- Plane Feature-Rollouts mit dem [Node-Features-Cookbook](docs/cookbooks/node-features.md).
- Kuratiere Abkürzungen für Power-User über das [Favorites-Cookbook](docs/cookbooks/favorites.md).
- Verbinde Slack-Workspaces mit dem [Slack-Bot-Onboarding-Cookbook](docs/cookbooks/slack-bot-onboarding.md).

## Support

Kontakt per [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) oder besuche unsere [Webseite](https://www.gelectriic.com/) für [professionelle Dienstleistungen](https://de.wikipedia.org/wiki/Dienstleistung) und [kommerziellen Support](https://de.wikipedia.org/wiki/Technischer_Support).

## Projektleitlinien

- [AGENTS](AGENTS.md) – Betriebsleitfaden für Repository-Workflows, Tests und Release-Management.
- [DESIGN](DESIGN.md) – Gestaltungs-, UX- und Branding-Richtlinien, die für alle Oberflächen gelten.

## Über mich

> "Wie bitte, du willst auch etwas über mich wissen? Nun, ich mag es, [Software zu entwickeln](https://de.wikipedia.org/wiki/Softwareentwicklung), [Pen-&-Paper-Rollenspiele](https://de.wikipedia.org/wiki/Rollenspiel), lange Spaziergänge am [Strand](https://de.wikipedia.org/wiki/Strand) und eine vierte geheime Sache."
> --Arthexis
