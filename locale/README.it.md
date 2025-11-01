# Costellazione Arthexis

[![Copertura](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![Copertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)


## Scopo

Costellazione Arthexis è una [suite software](https://it.wikipedia.org/wiki/Suite_informatiche) [guidata dalla narrazione](https://it.wikipedia.org/wiki/Narrazione) basata su [Django](https://www.djangoproject.com/) che centralizza gli strumenti per gestire l'[infrastruttura di ricarica dei veicoli elettrici](https://it.wikipedia.org/wiki/Stazione_di_ricarica) e orchestrare [prodotti](https://it.wikipedia.org/wiki/Prodotto_(economia)) e [servizi](https://it.wikipedia.org/wiki/Servizio_(economia)) legati all'[energia](https://it.wikipedia.org/wiki/Energia).

## Caratteristiche attuali

- Compatibile con il [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) come sistema centrale. Le azioni supportate sono riassunte di seguito.

  | Direzione | Azione | Cosa facciamo |
  | --- | --- | --- |
  | Punto di ricarica → CSMS | `Authorize` | Convalidiamo richieste di autorizzazione RFID o token prima dell'inizio della sessione. |
  | Punto di ricarica → CSMS | `BootNotification` | Registriamo il punto di ricarica e aggiorniamo identità, firmware e stato. |
  | Punto di ricarica → CSMS | `DataTransfer` | Accettiamo payload specifici del fornitore e registriamo gli esiti. |
  | Punto di ricarica → CSMS | `DiagnosticsStatusNotification` | Monitoriamo l'avanzamento dei caricamenti diagnostici avviati dal backoffice. |
  | Punto di ricarica → CSMS | `FirmwareStatusNotification` | Monitoriamo le fasi degli aggiornamenti firmware segnalate dai punti di ricarica. |
  | Punto di ricarica → CSMS | `Heartbeat` | Manteniamo viva la sessione websocket e aggiorniamo il timestamp dell'ultima attività. |
  | Punto di ricarica → CSMS | `MeterValues` | Salviamo letture periodiche di energia e potenza durante la transazione. |
  | Punto di ricarica → CSMS | `StartTransaction` | Creiamo sessioni di ricarica con valori iniziali del contatore e dati identificativi. |
  | Punto di ricarica → CSMS | `StatusNotification` | Riflettiamo in tempo reale disponibilità e stati di guasto dei connettori. |
  | Punto di ricarica → CSMS | `StopTransaction` | Chiudiamo le sessioni di ricarica registrando valori finali e motivazioni di chiusura. |
  | CSMS → Punto di ricarica | `ChangeAvailability` | Impostiamo connettori o stazione tra operativa e fuori servizio. |
  | CSMS → Punto di ricarica | `DataTransfer` | Inviamo comandi specifici del fornitore e registriamo la risposta del punto di ricarica. |
  | CSMS → Punto di ricarica | `GetConfiguration` | Interroghiamo il dispositivo sui valori correnti delle chiavi di configurazione monitorate. |
  | CSMS → Punto di ricarica | `RemoteStartTransaction` | Avviamo da remoto una sessione di ricarica per clienti o token identificati. |
  | CSMS → Punto di ricarica | `RemoteStopTransaction` | Interrompiamo da remoto sessioni attive dal centro di controllo. |
  | CSMS → Punto di ricarica | `Reset` | Richiediamo un riavvio soft o hard per ripristinare guasti. |
  | CSMS → Punto di ricarica | `TriggerMessage` | Chiediamo al dispositivo un aggiornamento immediato (ad esempio stato o diagnostica). |

  **Roadmap OCPP 1.6.** Le seguenti azioni del catalogo sono nel nostro backlog: `CancelReservation`, `ChangeConfiguration`, `ClearCache`, `ClearChargingProfile`, `GetCompositeSchedule`, `GetDiagnostics`, `GetLocalListVersion`, `ReserveNow`, `SendLocalList`, `SetChargingProfile`, `UnlockConnector`, `UpdateFirmware`.

- Integrazione [API](https://it.wikipedia.org/wiki/Application_programming_interface) con [Odoo](https://www.odoo.com/) per:
  - Sincronizzare le credenziali dei dipendenti tramite `res.users`
  - Consultare il catalogo prodotti tramite `product.product`
- Funziona su [Windows 11](https://www.microsoft.com/windows/windows-11) e [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Testato per il [Raspberry Pi 4 Modello B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Architettura dei ruoli

Costellazione Arthexis è distribuita in quattro ruoli di nodo pensati per diversi scenari di distribuzione.

<table border="1" cellpadding="8" cellspacing="0">
  <thead>
    <tr>
      <th align="left">Ruolo</th>
      <th align="left">Descrizione e funzionalità comuni</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td valign="top"><strong>Terminal</strong></td>
      <td valign="top"><strong>Ricerca e sviluppo per singolo utente</strong><br />Funzionalità: GUI Toast</td>
    </tr>
    <tr>
      <td valign="top"><strong>Control</strong></td>
      <td valign="top"><strong>Test di dispositivi singoli e appliance per compiti speciali</strong><br />Funzionalità: AP Public Wi-Fi, Celery Queue, GUI Toast, LCD Screen, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Satellite</strong></td>
      <td valign="top"><strong>Periferia multi-dispositivo, rete e acquisizione dati</strong><br />Funzionalità: AP Router, Celery Queue, NGINX Server, RFID Scanner</td>
    </tr>
    <tr>
      <td valign="top"><strong>Watchtower</strong></td>
      <td valign="top"><strong>Cloud multiutente e orchestrazione</strong><br />Funzionalità: Celery Queue, NGINX Server</td>
    </tr>
  </tbody>
</table>

## Guida rapida

### 1. Clonare
- **[Linux](https://it.wikipedia.org/wiki/Linux)**: apri un [terminale](https://it.wikipedia.org/wiki/Interfaccia_a_riga_di_comando) ed esegui `git clone https://github.com/arthexis/arthexis.git`.
- **[Windows](https://it.wikipedia.org/wiki/Microsoft_Windows)**: apri [PowerShell](https://learn.microsoft.com/powershell/) o [Git Bash](https://gitforwindows.org/) ed esegui lo stesso comando.

### 2. Avvio e arresto
I nodi Terminal possono avviarsi direttamente con gli script sottostanti senza installazione; i ruoli Control, Satellite e Watchtower richiedono prima l'installazione. Entrambi i metodi ascoltano su [`http://localhost:8000/`](http://localhost:8000/) per impostazione predefinita.

- **[VS Code](https://code.visualstudio.com/)**
   - Apri la cartella e vai al pannello **Run and Debug** (`Ctrl+Shift+D`).
   - Seleziona la configurazione **Run Server** (o **Debug Server**).
   - Premi il pulsante verde di avvio. Arresta il server con il quadrato rosso (`Shift+F5`).

- **[Shell](https://it.wikipedia.org/wiki/Shell_(informatica))**
   - Linux: esegui [`./start.sh`](start.sh) e arresta con [`./stop.sh`](stop.sh).
   - Windows: esegui [`start.bat`](start.bat) e interrompi con `Ctrl+C`.

### 3. Installare e aggiornare
- **Linux:**
   - Esegui [`./install.sh`](install.sh) con un flag per il ruolo del nodo:
     - `--terminal` – impostazione predefinita se non specificato e consigliato se non sei sicuro. I nodi Terminal possono anche utilizzare gli script sopra per avviare/arrestare senza installazione.
     - `--control` – prepara l'appliance di test per singolo dispositivo.
     - `--satellite` – configura il nodo perimetrale di acquisizione dati.
     - `--constellation` – abilita lo stack di orchestrazione multiutente.
   - Usa `./install.sh --help` per elencare tutte le opzioni disponibili se hai bisogno di personalizzare il nodo oltre le impostazioni del ruolo.
   - Aggiorna con [`./upgrade.sh`](upgrade.sh).
   - Consulta il [Manuale degli script di installazione e ciclo di vita](docs/development/install-lifecycle-scripts-manual.md) per l'elenco completo dei flag e le note operative.

- **Windows:**
   - Esegui [`install.bat`](install.bat) per installare (ruolo Terminal) e [`upgrade.bat`](upgrade.bat) per aggiornare.
   - Non è necessario installare per avviare in modalità Terminal (predefinita).

### 4. Amministrazione
Visita [`http://localhost:8000/admin/`](http://localhost:8000/admin/) per il [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) e [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) per gli [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Usa `--port` con gli script di avvio o l'installer quando devi esporre una porta diversa.

## Sigilli

I sigilli sono token tra parentesi quadre come `[ENV.SMTP_PASSWORD]` che Arthexis espande in fase di esecuzione. Consentono di fare riferimento a segreti di configurazione, metadati di sistema o record memorizzati in altre app senza duplicare i valori nel progetto.

### Sintassi in breve

- `[PREFIX.KEY]` &mdash; restituisce un campo o attributo. Trattini e maiuscole/minuscole vengono normalizzati automaticamente.
- `[PREFIX=IDENTIFICATIVO.CAMPO]` &mdash; seleziona un record specifico tramite chiave primaria o qualsiasi campo univoco.
- `[PREFIX:CAMPO=VALORE.ATTRIBUTO]` &mdash; filtra usando un campo personalizzato invece della chiave primaria.
- `[PREFIX.CAMPO=[ALTRO.SIGILLO]]` &mdash; permette di annidare i sigilli; il valore dopo `=` viene risolto prima del token esterno.
- `[PREFIX]` &mdash; con prefissi di entità restituisce l'oggetto serializzato in JSON; con prefissi di configurazione produce una stringa vuota se la chiave è assente.

La piattaforma include tre prefissi di configurazione:

- `ENV` legge le variabili d'ambiente.
- `CONF` legge le impostazioni di Django.
- `SYS` espone informazioni di sistema calcolate, come metadati di build.

Prefissi aggiuntivi vengono definiti tramite **Sigil Roots**, che associano un codice breve (ad esempio `ROLE`, `ODOO` o `USER`) a un modello Django. Puoi consultarli in **Admin &rarr; Sigil Builder** (`/admin/sigil-builder/`), che offre anche una console di test.

I prefissi sconosciuti restano invariati (ad esempio `[UNKNOWN.VALUE]`) e vengono registrati nei log. Quando è installata la CLI opzionale `gway`, il risolutore prova a delegare i token non risolti prima di restituire il testo originale.

## Supporto

Contattaci all'indirizzo [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visita la nostra [pagina web](https://www.gelectriic.com/) per [servizi professionali](https://it.wikipedia.org/wiki/Servizio_professionale) e [supporto commerciale](https://it.wikipedia.org/wiki/Supporto_tecnico).

## Linee guida del progetto

- [AGENTS](AGENTS.md) – manuale operativo per i flussi di lavoro del repository, i test e la gestione delle release.
- [DESIGN](DESIGN.md) – linee guida su design visivo, UX e branding che tutte le interfacce devono seguire.

## Chi sono

> "Cosa? Vuoi sapere qualcosa anche su di me? Beh, mi piace [sviluppare software](https://it.wikipedia.org/wiki/Sviluppo_software), i [giochi di ruolo](https://it.wikipedia.org/wiki/Gioco_di_ruolo), lunghe passeggiate sulla [spiaggia](https://it.wikipedia.org/wiki/Spiaggia) e una quarta cosa segreta."
> --Arthexis
