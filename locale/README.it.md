# Costellazione Arthexis

[![Copertura](https://raw.githubusercontent.com/arthexis/arthexis/main/coverage.svg)](https://github.com/arthexis/arthexis/actions/workflows/coverage.yml) [![Copertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Copertura OCPP 2.1](https://raw.githubusercontent.com/arthexis/arthexis/main/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)


## Scopo

Costellazione Arthexis è una [suite software](https://it.wikipedia.org/wiki/Suite_informatiche) basata su [Django](https://www.djangoproject.com/) che centralizza gli strumenti per gestire l'[infrastruttura di ricarica dei veicoli elettrici](https://it.wikipedia.org/wiki/Stazione_di_ricarica) e orchestrare [prodotti](https://it.wikipedia.org/wiki/Prodotto_(economia)) e [servizi](https://it.wikipedia.org/wiki/Servizio_(economia)) legati all'[energia](https://it.wikipedia.org/wiki/Energia).

Visita il [Report del changelog](http://localhost:8888/changelog/) pubblico per consultare aggiornamenti inediti e storici generati direttamente dai commit git.

Consulta il nuovo [articolo degli sviluppatori](http://localhost:8888/articles/protoline-integration/) con le novità del prossimo rilascio e i dettagli del rollout.

## Caratteristiche attuali

- Compatibile con il [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) come sistema centrale. Le azioni supportate sono riassunte di seguito.

  **Punto di ricarica → CSMS**

  | Azione | Versione 1.6 | Versione 2.1 | Cosa facciamo |
  | --- | --- | --- | --- |
  | `Authorize` | ✅ | ✅ | Convalidiamo richieste di autorizzazione RFID o token prima dell'inizio della sessione. |
  | `BootNotification` | ✅ | ✅ | Registriamo il punto di ricarica e aggiorniamo identità, firmware e stato. |
  | `DataTransfer` | ✅ | ✅ | Accettiamo payload specifici del fornitore e registriamo gli esiti. |
  | `DiagnosticsStatusNotification` | ✅ | — | Monitoriamo l'avanzamento dei caricamenti diagnostici avviati dal backoffice. |
  | `FirmwareStatusNotification` | ✅ | ✅ | Monitoriamo le fasi degli aggiornamenti firmware segnalate dai punti di ricarica. |
  | `Heartbeat` | ✅ | ✅ | Manteniamo viva la sessione websocket e aggiorniamo il timestamp dell'ultima attività. |
  | `LogStatusNotification` | — | ✅ | Monitoriamo l'avanzamento dei caricamenti dei log dal punto di ricarica per la supervisione diagnostica. |
  | `MeterValues` | ✅ | ✅ | Salviamo letture periodiche di energia e potenza durante la transazione. |
  | `SecurityEventNotification` | — | ✅ | Registriamo gli eventi di sicurezza segnalati dai punti di ricarica per la tracciabilità. |
  | `StartTransaction` | ✅ | — | Creiamo sessioni di ricarica con valori iniziali del contatore e dati identificativi. |
  | `StatusNotification` | ✅ | ✅ | Riflettiamo in tempo reale disponibilità e stati di guasto dei connettori. |
  | `StopTransaction` | ✅ | — | Chiudiamo le sessioni di ricarica registrando valori finali e motivazioni di chiusura. |

  **CSMS → Punto di ricarica**

  | Azione | Versione 1.6 | Versione 2.1 | Cosa facciamo |
  | --- | --- | --- | --- |
  | `CancelReservation` | ✅ | ✅ | Annulliamo prenotazioni in sospeso e liberiamo i connettori direttamente dal centro di controllo. |
  | `ChangeAvailability` | ✅ | ✅ | Impostiamo connettori o stazione tra operativa e fuori servizio. |
  | `ChangeConfiguration` | ✅ | — | Aggiorniamo le impostazioni supportate del charger e registriamo i valori applicati nel centro di controllo. |
  | `ClearCache` | ✅ | ✅ | Svuotiamo le cache di autorizzazione locali per forzare nuove verifiche dal CSMS. |
  | `DataTransfer` | ✅ | ✅ | Inviamo comandi specifici del fornitore e registriamo la risposta del punto di ricarica. |
  | `GetConfiguration` | ✅ | — | Interroghiamo il dispositivo sui valori correnti delle chiavi di configurazione monitorate. |
  | `GetLocalListVersion` | ✅ | ✅ | Recuperiamo la versione corrente della whitelist RFID e sincronizziamo le voci segnalate dal punto di ricarica. |
  | `RemoteStartTransaction` | ✅ | — | Avviamo da remoto una sessione di ricarica per clienti o token identificati. |
  | `RemoteStopTransaction` | ✅ | — | Interrompiamo da remoto sessioni attive dal centro di controllo. |
  | `ReserveNow` | ✅ | ✅ | Prenotiamo i connettori per le sessioni future con assegnazione automatica e tracciamento della conferma. |
  | `Reset` | ✅ | ✅ | Richiediamo un riavvio soft o hard per ripristinare guasti. |
  | `SendLocalList` | ✅ | ✅ | Pubbliciamo gli RFID rilasciati e approvati come lista di autorizzazione locale del punto di ricarica. |
  | `TriggerMessage` | ✅ | ✅ | Chiediamo al dispositivo un aggiornamento immediato (ad esempio stato o diagnostica). |
  | `UnlockConnector` | ✅ | ✅ | Sblocchiamo i connettori bloccati senza intervento in loco. |
  | `UpdateFirmware` | ✅ | ✅ | Distribuiamo pacchetti firmware ai charger con token di download sicuri e tracciamo le risposte di installazione. |

  **Roadmap OCPP.** Esplora il lavoro pianificato per i cataloghi OCPP 1.6 e 2.1 nel [cookbook della roadmap OCPP](docs/cookbooks/ocpp-roadmap.md).

- Prenotazioni dei punti di ricarica con assegnazione automatica del connettore, collegamento agli Energy Account e ai RFID, conferma EVCS e annullamento dal centro di controllo.

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
I nodi Terminal possono avviarsi direttamente con gli script sottostanti senza installazione; i ruoli Control, Satellite e Watchtower richiedono prima l'installazione. Entrambi i metodi ascoltano su [`http://localhost:8888/`](http://localhost:8888/) per impostazione predefinita.

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
     - `--watchtower` – abilita lo stack di orchestrazione multiutente.
   - Usa `./install.sh --help` per elencare tutte le opzioni disponibili se hai bisogno di personalizzare il nodo oltre le impostazioni del ruolo.
   - Aggiorna con [`./upgrade.sh`](upgrade.sh).
   - Consulta il [Manuale degli script di installazione e ciclo di vita](docs/development/install-lifecycle-scripts-manual.md) per l'elenco completo dei flag e le note operative.
   - Consulta la [Guida all'aggiornamento](docs/UPGRADE.md) per i passaggi manuali richiesti quando alcune migrazioni non vengono più automatizzate.

- **Windows:**
   - Esegui [`install.bat`](install.bat) per installare (ruolo Terminal) e [`upgrade.bat`](upgrade.bat) per aggiornare.
   - Non è necessario installare per avviare in modalità Terminal (predefinita).

### 4. Amministrazione
- Accedi al [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) su [`http://localhost:8888/admin/`](http://localhost:8888/admin/) per verificare e gestire i dati in tempo reale. Usa `--port` con gli script di avvio o l'installer quando devi esporre una porta diversa.
- Consulta gli [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/) su [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) per leggere la documentazione API generata automaticamente dai tuoi modelli.
- Canali di aggiornamento: l'aggiornamento automatico è attivo di default sul canale stabile (controlli ogni 24 ore e installazione solo se la revisione coincide con l'indice pacchetti). Passa al canale instabile con `--unstable`/`--latest` per seguire le revisioni del branch principale ogni 10 minuti oppure usa `--fixed` per disabilitare l'automazione.
- Segui la [Guida all'installazione e all'amministrazione](docs/cookbooks/install-start-stop-upgrade-uninstall.md) per attività di deployment, ciclo di vita e runbook operativi.
- Esegui onboarding e manutenzione dei caricabatterie con il [Cookbook Connettività e Manutenzione EVCS](docs/cookbooks/evcs-connectivity-maintenance.md).
- Configura i gateway di pagamento con il [Cookbook dei processori di pagamento](docs/cookbooks/payment-processors.md).
- Fai riferimento al [Cookbook dei sigilli](docs/cookbooks/sigils.md) quando configuri impostazioni basate su token tra gli ambienti.
- Gestisci esportazioni, importazioni e tracciamenti con il [Cookbook sui dati utente](docs/cookbooks/user-data.md).
- Pianifica le strategie di rilascio delle funzionalità con il [Cookbook sulle funzionalità dei nodi](docs/cookbooks/node-features.md).
- Cura scorciatoie per gli utenti esperti tramite il [Cookbook dei preferiti](docs/cookbooks/favorites.md).
- Collega i workspace Slack con il [Cookbook di onboarding dello Slack Bot](docs/cookbooks/slack-bot-onboarding.md).

## Supporto

Contattaci all'indirizzo [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visita la nostra [pagina web](https://www.gelectriic.com/) per [servizi professionali](https://it.wikipedia.org/wiki/Servizio_professionale) e [supporto commerciale](https://it.wikipedia.org/wiki/Supporto_tecnico).

## Linee guida del progetto

- [AGENTS](AGENTS.md) – manuale operativo per i flussi di lavoro del repository, i test e la gestione delle release.
- [DESIGN](DESIGN.md) – linee guida su design visivo, UX e branding che tutte le interfacce devono seguire.

## Chi sono

> "Cosa? Vuoi sapere qualcosa anche su di me? Beh, mi piace [sviluppare software](https://it.wikipedia.org/wiki/Sviluppo_software), i [giochi di ruolo](https://it.wikipedia.org/wiki/Gioco_di_ruolo), lunghe passeggiate sulla [spiaggia](https://it.wikipedia.org/wiki/Spiaggia) e una quarta cosa segreta."
> --Arthexis
