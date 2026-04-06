# Constellation

[![OCPP 1.6-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.0.1-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![OCPP 2.1-Abdeckung](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)
[![Install CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/install-hourly.yml?branch=main&label=Install%20CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/install-hourly.yml) [![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/) [![Lizenz: ARG 1.0](https://raw.githubusercontent.com/arthexis/arthexis/main/static/docs/badges/license-arthexis-reciprocity.svg)](https://github.com/arthexis/arthexis/blob/main/LICENSE)


## Zweck

Die Arthexis-Konstellation ist eine auf Django basierende Softwaresuite, die Werkzeuge zur Verwaltung der Ladeinfrastruktur für Elektrofahrzeuge sowie zur Orchestrierung von energiebezogenen Produkten und Dienstleistungen zentralisiert.

Besuche den [Changelog-Bericht](https://arthexis.com/changelog/), um vergangene und geplante Funktionen sowie weitere Updates zu entdecken.

## Suite-Funktionen

- Kompatibel mit dem [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) standardmäßig, während Ladepunkte auf neuere Protokolle aktualisieren können, wenn sie diese unterstützen.

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

- Ladepunktreservierungen mit automatischer Anschlusswahl, Verknüpfung zu Energiekonten und RFID-Tags, EVCS-Bestätigung sowie Stornierung über die Leitwarte.
- Details findest du im [Odoo-API-Integrations-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/odoo-integrations.md) zur Synchronisierung von Mitarbeiterzugängen über `res.users` und Produktkatalogabfragen über `product.product`.
- Läuft auf Windows 11 und Ubuntu 24.
- Getestet für den Raspberry Pi 4 Model B.

Projekt in offener, sehr aktiver Entwicklung.

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
- **Linux**: Öffne ein Terminal und führe `git clone https://github.com/arthexis/arthexis.git` aus.
- **Windows**: Öffne PowerShell oder Git Bash und führe denselben Befehl aus.

### 2. Starten und Stoppen
Terminal-Knoten können direkt mit den untenstehenden Skripten ohne Installation gestartet werden; die Rollen Control, Satellite und Watchtower müssen vorher installiert werden. Beide Ansätze lauschen standardmäßig auf `localhost:8888/`.

- **VS Code**
   - Ordner öffnen und zum Bereich **Run and Debug** (`Ctrl+Shift+D`) wechseln.
   - Die Konfiguration **Run Server** (oder **Debug Server**) auswählen.
   - Auf den grünen Startknopf klicken. Den Server mit dem roten Quadrat (`Shift+F5`) anhalten.

- **Shell**
   - Linux: [`./start.sh`](https://github.com/arthexis/arthexis/blob/main/start.sh) ausführen und mit [`./stop.sh`](https://github.com/arthexis/arthexis/blob/main/stop.sh) anhalten.
   - Windows: [`start.bat`](https://github.com/arthexis/arthexis/blob/main/start.bat) ausführen und mit `Ctrl+C` beenden.

### 3. Installieren und Aktualisieren
- **Linux:**
   - [`./install.sh`](https://github.com/arthexis/arthexis/blob/main/install.sh) mit einem Flag für die Node-Rolle ausführen; siehe die obige Tabelle zur Rollenarchitektur für die rollenspezifischen Optionen und Standardwerte.
   - `./install.sh --help` zeigt alle verfügbaren Optionen, falls du die Konfiguration über die Rollenvorgaben hinaus anpassen möchtest.
   - Aktualisieren mit [`./upgrade.sh`](https://github.com/arthexis/arthexis/blob/main/upgrade.sh).
   - Lies das [Manual zu Installations- & Lifecycle-Skripten](https://github.com/arthexis/arthexis/blob/main/docs/development/install-lifecycle-scripts-manual.md) für vollständige Flag-Beschreibungen und Betriebsdetails.
   - Sieh dir den [Auto-Upgrade-Flow](https://github.com/arthexis/arthexis/blob/main/docs/auto-upgrade.md) an, um zu verstehen, wie delegierte Upgrades laufen und wie du sie überwachst.

- **Windows:**
   - [`install.bat`](https://github.com/arthexis/arthexis/blob/main/install.bat) zur Installation (Terminal-Rolle) und [`upgrade.bat`](https://github.com/arthexis/arthexis/blob/main/upgrade.bat) zum Aktualisieren ausführen.
   - Für den Start im Terminalmodus (Standard) ist keine Installation erforderlich.

### 4. Administration
- Greife über `localhost:8888/admin/` auf den Django-Admin zu, um Live-Daten zu prüfen und zu pflegen. Verwende `--port` mit den Startskripten oder dem Installer, wenn du einen anderen Port freigeben musst.
- Durchstöbere die admindocs unter `localhost:8888/admindocs/`, um automatisch generierte API-Dokumentation deiner Modelle zu lesen.
- Upgrade-Kanal-Schema:

| Kanal | Prüfintervall | Zweck | Aktivierungsflag |
| --- | --- | --- | --- |
| Stable | Wöchentlich (Donnerstag vor 5:00 Uhr) | Folgt Release-Revisionen mit automatischen Wochenprüfungen. | `--stable` |
| Latest | Täglich (zur selben Stunde) | Folgt den neuesten Mainline-Revisionen mit täglichen Prüfungen. | `--latest` / `-l` oder `--unstable` |
| Manual | Keine (nur manuelle Upgrades) | Deaktiviert die automatische Upgrade-Schleife für volle Betreiberkontrolle. Dieses Verhalten ist der Standard, wenn kein Kanal angegeben wird. | _Upgrades bei Bedarf ohne Kanal-Flag ausführen._ |

- Folge dem [Installations- und Administrationshandbuch](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/install-start-stop-upgrade-uninstall.md) für Deployment, Lifecycle-Aufgaben und operative Runbooks.
- Nimm Ladepunkte mit dem [EVCS-Konnektivitäts- und Wartungs-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/evcs-connectivity-maintenance.md) in Betrieb und halte sie instand.
- Konfiguriere Zahlungs-Gateways mit dem [Payment Processors Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/payment-processors.md).
- Nutze das [Sigil-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/sigils.md), wenn du tokenbasierte Einstellungen über Umgebungen hinweg konfigurierst.
- Verstehe Seed-Fixtures und benutzerspezifische Dateien mit [Lokale Node-Daten verwalten](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/managing-local-node-data.md).
- Verwalte Exporte, Importe und Prüfprotokolle mit dem [User-Data-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/user-data.md).
- Plane Feature-Rollouts mit dem [Node-Features-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/node-features.md).
- Kuratiere Abkürzungen für Power-User über das [Favorites-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/favorites.md).
- Verbinde Slack-Workspaces mit dem [Slack-Bot-Onboarding-Cookbook](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/slack-bot-onboarding.md).

### 5. Entwicklung
- Durchsuche die [Entwickler-Dokumentationsbibliothek](../../../docs/index.md) für Architekturreferenzen, Protokollhandbücher und Beitrags-Workflows.
- Hinweis: Dieser Link zeigt auf die Dokumentation im Repository und nicht auf eine Laufzeit-Webroute.

## Support

Die Arthexis-Konstellation befindet sich weiterhin in sehr aktiver Entwicklung und erhält täglich neue Funktionen.

Wenn du unsere Suite für deine Energieprojekte einsetzen möchtest, erreichst du uns unter [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) oder über unsere [Webseite](https://www.gelectriic.com/) für professionelle Dienstleistungen und kommerziellen Support.

## Lizenz und Förderung

Arthexis wird unter der Arthexis Reciprocity General License 1.0 veröffentlicht. Neben Code, Dokumentation, Reviews und Wartung betrachten wir auch das Sponsoring von Arthexis sowie bezahlte oder ehrenamtliche Arbeit für die Open-Source-Abhängigkeiten, auf denen wir aufbauen, als gültigen und wichtigen Beitrag.

Wenn Arthexis deinem Team hilft, lies bitte die Lizenzbedingungen in [`LICENSE`](../../../LICENSE) und erwäge, die Maintainer der Bibliotheken, Frameworks und Infrastrukturprojekte zu sponsoren oder direkt zu unterstützen, die diese Suite möglich machen. Die Unterstützung dieser Abhängigkeiten stärkt das gesamte Arthexis-Ökosystem.
