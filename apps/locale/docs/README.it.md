# Constellation

[![CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/ci.yml?branch=main&label=CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/) [![Copertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Copertura OCPP 2.0.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Copertura OCPP 2.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Licenza: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE) ![Watchtowers](https://raw.githubusercontent.com/arthexis/arthexis/main/media/watchtowers.svg)


## Scopo

Costellazione Arthexis è una [suite software](https://it.wikipedia.org/wiki/Suite_informatiche) basata su [Django](https://www.djangoproject.com/) che centralizza gli strumenti per gestire l'[infrastruttura di ricarica dei veicoli elettrici](https://it.wikipedia.org/wiki/Stazione_di_ricarica) e orchestrare [prodotti](https://it.wikipedia.org/wiki/Prodotto_(economia)) e [servizi](https://it.wikipedia.org/wiki/Servizio_(economia)) legati all'[energia](https://it.wikipedia.org/wiki/Energia).

Visita il [Report del changelog](https://arthexis.com/changelog/) per esplorare funzionalità passate e future insieme ad altri aggiornamenti.

## Caratteristiche della suite

- Compatibile con l'[Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) per impostazione predefinita, consentendo ai punti di ricarica di aggiornarsi ai protocolli più recenti se li supportano.

  **Punto di ricarica → CSMS**

  | Azione | 1.6 | 2.0.1 | 2.1 | Cosa facciamo |
  | --- | --- | --- | --- | --- |
  | `Authorize` | ✅ | ✅ | ✅ | Convalidiamo richieste di autorizzazione RFID o token prima dell'inizio della sessione. |
  | `BootNotification` | ✅ | ✅ | ✅ | Registriamo il punto di ricarica e aggiorniamo identità, firmware e stato. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Accettiamo payload specifici del fornitore e registriamo gli esiti. |
  | `DiagnosticsStatusNotification` | ✅ | — | — | Monitoriamo l'avanzamento dei caricamenti diagnostici avviati dal backoffice. |
  | `FirmwareStatusNotification` | ✅ | ✅ | ✅ | Monitoriamo le fasi degli aggiornamenti firmware segnalate dai punti di ricarica. |
  | `Heartbeat` | ✅ | ✅ | ✅ | Manteniamo viva la sessione websocket e aggiorniamo il timestamp dell'ultima attività. |
  | `LogStatusNotification` | — | ✅ | ✅ | Monitoriamo l'avanzamento dei caricamenti dei log dal punto di ricarica per la supervisione diagnostica. |
  | `MeterValues` | ✅ | ✅ | ✅ | Salviamo letture periodiche di energia e potenza durante la transazione. |
  | `SecurityEventNotification` | — | ✅ | ✅ | Registriamo gli eventi di sicurezza segnalati dai punti di ricarica per la tracciabilità. |
  | `StartTransaction` | ✅ | — | — | Creiamo sessioni di ricarica con valori iniziali del contatore e dati identificativi. |
  | `StatusNotification` | ✅ | ✅ | ✅ | Riflettiamo in tempo reale disponibilità e stati di guasto dei connettori. |
  | `StopTransaction` | ✅ | — | — | Chiudiamo le sessioni di ricarica registrando valori finali e motivazioni di chiusura. |

  **CSMS → Punto di ricarica**

  | Azione | 1.6 | 2.0.1 | 2.1 | Cosa facciamo |
  | --- | --- | --- | --- | --- |
  | `CancelReservation` | ✅ | ✅ | ✅ | Annulliamo prenotazioni in sospeso e liberiamo i connettori direttamente dal centro di controllo. |
  | `ChangeAvailability` | ✅ | ✅ | ✅ | Impostiamo connettori o stazione tra operativa e fuori servizio. |
  | `ChangeConfiguration` | ✅ | — | — | Aggiorniamo le impostazioni supportate del charger e registriamo i valori applicati nel centro di controllo. |
  | `ClearCache` | ✅ | ✅ | ✅ | Svuotiamo le cache di autorizzazione locali per forzare nuove verifiche dal CSMS. |
  | `DataTransfer` | ✅ | ✅ | ✅ | Inviamo comandi specifici del fornitore e registriamo la risposta del punto di ricarica. |
  | `GetConfiguration` | ✅ | — | — | Interroghiamo il dispositivo sui valori correnti delle chiavi di configurazione monitorate. |
  | `GetDiagnostics` | ✅ | — | — | Richiediamo il caricamento di un archivio di diagnostica su un URL firmato per la risoluzione dei problemi. |
  | `GetLocalListVersion` | ✅ | ✅ | ✅ | Recuperiamo la versione corrente della whitelist RFID e sincronizziamo le voci segnalate dal punto di ricarica. |
  | `RemoteStartTransaction` | ✅ | — | — | Avviamo da remoto una sessione di ricarica per clienti o token identificati. |
  | `RemoteStopTransaction` | ✅ | — | — | Interrompiamo da remoto sessioni attive dal centro di controllo. |
  | `ReserveNow` | ✅ | ✅ | ✅ | Prenotiamo i connettori per le sessioni future con assegnazione automatica e tracciamento della conferma. |
  | `Reset` | ✅ | ✅ | ✅ | Richiediamo un riavvio soft o hard per ripristinare guasti. |
  | `SendLocalList` | ✅ | ✅ | ✅ | Pubbliciamo gli RFID rilasciati e approvati come lista di autorizzazione locale del punto di ricarica. |
  | `TriggerMessage` | ✅ | ✅ | ✅ | Chiediamo al dispositivo un aggiornamento immediato (ad esempio stato o diagnostica). |
  | `UnlockConnector` | ✅ | ✅ | ✅ | Sblocchiamo i connettori bloccati senza intervento in loco. |
  | `UpdateFirmware` | ✅ | ✅ | ✅ | Distribuiamo pacchetti firmware ai charger con token di download sicuri e tracciamo le risposte di installazione. |

  **Roadmap OCPP.** Esplora il lavoro pianificato per i cataloghi OCPP 1.6, 2.0.1 e 2.1 nel [cookbook della roadmap OCPP](apps/docs/cookbooks/ocpp-roadmap.md).

- Prenotazioni dei punti di ricarica con assegnazione automatica del connettore, collegamento agli Energy Account e ai RFID, conferma EVCS e annullamento dal centro di controllo.
- Scopri il [cookbook di integrazione API con Odoo](apps/docs/cookbooks/odoo-integrations.md) per i dettagli sulle sincronizzazioni delle credenziali dei dipendenti tramite `res.users` e sulle ricerche del catalogo prodotti tramite `product.product`.
- Funziona su [Windows 11](https://www.microsoft.com/windows/windows-11) e [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Testato per il [Raspberry Pi 4 Modello B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

Progetto in sviluppo aperto e molto attivo.

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

Lo script di configurazione dei ruoli si trova ora nella directory principale come [`./configure.sh`](configure.sh). Gli helper di ciclo di vita per servizi e aggiornamenti vivono in [`scripts/`](scripts): `scripts/service-start.sh`, `scripts/nginx-setup.sh` e `scripts/delegated-upgrade.sh`. Gli helper di manutenzione legacy (`db-setup.sh`, `db-migrate.sh`, `renew-certs.sh`, `restore-fs.sh`, `change-hostname.sh`, `email-setup.sh`, `network-setup.sh` e `ws.sh`) sono stati rimossi.

### 3. Installare e aggiornare
- **Linux:**
   - Esegui [`./install.sh`](install.sh) con un flag per il ruolo del nodo; consulta la tabella sull'architettura dei ruoli qui sopra per le opzioni e i valori predefiniti di ciascun ruolo.
   - Usa `./install.sh --help` per elencare tutte le opzioni disponibili se hai bisogno di personalizzare il nodo oltre le impostazioni del ruolo.
   - Aggiorna con [`./upgrade.sh`](upgrade.sh).
   - Consulta il [Manuale degli script di installazione e ciclo di vita](docs/development/install-lifecycle-scripts-manual.md) per l'elenco completo dei flag e le note operative.
   - Consulta il [Flusso di auto-aggiornamento](docs/auto-upgrade.md) per capire come vengono eseguiti gli upgrade delegati e come monitorarli.

- **Windows:**
   - Esegui [`install.bat`](install.bat) per installare (ruolo Terminal) e [`upgrade.bat`](upgrade.bat) per aggiornare.
   - Non è necessario installare per avviare in modalità Terminal (predefinita).

### 4. Amministrazione
- Accedi al [Django admin](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) su [`http://localhost:8000/admin/`](http://localhost:8000/admin/) per verificare e gestire i dati in tempo reale. Usa `--port` con gli script di avvio o l'installer quando devi esporre una porta diversa.
- Consulta gli [admindocs](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/) su [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) per leggere la documentazione API generata automaticamente dai tuoi modelli.
- Canali di aggiornamento: le nuove installazioni usano `--fixed` per impostazione predefinita e lasciano l'aggiornamento automatico disattivato. Attiva gli aggiornamenti automatici sul canale stabile con `--stable` (controlli settimanali il giovedì mattina, prima delle 5:00, allineati alle release), segui rapidamente le revisioni del branch principale con `--unstable` (controlli ogni 15 minuti) oppure usa il canale latest con `--latest` (controlli giornalieri alla stessa ora).
- Segui la [Guida all'installazione e all'amministrazione](apps/docs/cookbooks/install-start-stop-upgrade-uninstall.md) per attività di deployment, ciclo di vita e runbook operativi.
- Esegui onboarding e manutenzione dei caricabatterie con il [Cookbook Connettività e Manutenzione EVCS](apps/docs/cookbooks/evcs-connectivity-maintenance.md).
- Configura i gateway di pagamento con il [Cookbook dei processori di pagamento](apps/docs/cookbooks/payment-processors.md).
- Fai riferimento al [Cookbook dei sigilli](apps/docs/cookbooks/sigils.md) quando configuri impostazioni basate su token tra gli ambienti.
- Gestisci esportazioni, importazioni e tracciamenti con il [Cookbook sui dati utente](apps/docs/cookbooks/user-data.md).
- Pianifica le strategie di rilascio delle funzionalità con il [Cookbook sulle funzionalità dei nodi](apps/docs/cookbooks/node-features.md).
- Cura scorciatoie per gli utenti esperti tramite il [Cookbook dei preferiti](apps/docs/cookbooks/favorites.md).
- Collega i workspace Slack con il [Cookbook di onboarding dello Slack Bot](apps/docs/cookbooks/slack-bot-onboarding.md).

## Supporto

Costellazione Arthexis è ancora in fase di sviluppo molto attivo e ogni giorno vengono aggiunte nuove funzionalità.

Se decidi di utilizzare la nostra suite per i tuoi progetti energetici, puoi contattarci all'indirizzo [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visitare la nostra [pagina web](https://www.gelectriic.com/) per [servizi professionali](https://it.wikipedia.org/wiki/Servizio_professionale) e [supporto commerciale](https://it.wikipedia.org/wiki/Supporto_tecnico).

## Chi sono

> "Cosa? Vuoi sapere qualcosa anche su di me? Beh, mi piace [sviluppare software](https://it.wikipedia.org/wiki/Sviluppo_software), i [giochi di ruolo](https://it.wikipedia.org/wiki/Gioco_di_ruolo), lunghe passeggiate sulla [spiaggia](https://it.wikipedia.org/wiki/Spiaggia) e una quarta cosa segreta."
> --Arthexis
