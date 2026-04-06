# Constellation

[![Copertura OCPP 1.6](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Copertura OCPP 2.0.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp201_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md) [![Copertura OCPP 2.1](https://raw.githubusercontent.com/arthexis/arthexis/main/media/ocpp21_coverage.svg)](https://github.com/arthexis/arthexis/blob/main/docs/development/ocpp-user-manual.md)
[![Install CI](https://img.shields.io/github/actions/workflow/status/arthexis/arthexis/install-hourly.yml?branch=main&label=Install%20CI&cacheSeconds=300)](https://github.com/arthexis/arthexis/actions/workflows/install-hourly.yml) [![PyPI](https://img.shields.io/pypi/v/arthexis?label=PyPI)](https://pypi.org/project/arthexis/) [![Licenza: ARG 1.0](https://raw.githubusercontent.com/arthexis/arthexis/main/static/docs/badges/license-arthexis-reciprocity.svg)](https://github.com/arthexis/arthexis/blob/main/LICENSE)


## Scopo

Costellazione Arthexis è una suite software basata su Django che centralizza gli strumenti per gestire l'infrastruttura di ricarica dei veicoli elettrici e orchestrare prodotti e servizi legati all'energia.

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

- Prenotazioni dei punti di ricarica con assegnazione automatica del connettore, collegamento agli Energy Account e ai RFID, conferma EVCS e annullamento dal centro di controllo.
- Scopri il [cookbook di integrazione API con Odoo](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/odoo-integrations.md) per i dettagli sulle sincronizzazioni delle credenziali dei dipendenti tramite `res.users` e sulle ricerche del catalogo prodotti tramite `product.product`.
- Funziona su Windows 11 e Ubuntu 24.
- Testato per il Raspberry Pi 4 Modello B.

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
- **Linux**: apri un terminale ed esegui `git clone https://github.com/arthexis/arthexis.git`.
- **Windows**: apri PowerShell o Git Bash ed esegui lo stesso comando.

### 2. Avvio e arresto
I nodi Terminal possono avviarsi direttamente con gli script sottostanti senza installazione; i ruoli Control, Satellite e Watchtower richiedono prima l'installazione. Entrambi i metodi ascoltano su `localhost:8888/` per impostazione predefinita.

- **VS Code**
   - Apri la cartella e vai al pannello **Run and Debug** (`Ctrl+Shift+D`).
   - Seleziona la configurazione **Run Server** (o **Debug Server**).
   - Premi il pulsante verde di avvio. Arresta il server con il quadrato rosso (`Shift+F5`).

- **Shell**
   - Linux: esegui [`./start.sh`](https://github.com/arthexis/arthexis/blob/main/start.sh) e arresta con [`./stop.sh`](https://github.com/arthexis/arthexis/blob/main/stop.sh).
   - Windows: esegui [`start.bat`](https://github.com/arthexis/arthexis/blob/main/start.bat) e interrompi con `Ctrl+C`.

### 3. Installare e aggiornare
- **Linux:**
   - Esegui [`./install.sh`](https://github.com/arthexis/arthexis/blob/main/install.sh) con un flag per il ruolo del nodo; consulta la tabella sull'architettura dei ruoli qui sopra per le opzioni e i valori predefiniti di ciascun ruolo.
   - Usa `./install.sh --help` per elencare tutte le opzioni disponibili se hai bisogno di personalizzare il nodo oltre le impostazioni del ruolo.
   - Aggiorna con [`./upgrade.sh`](https://github.com/arthexis/arthexis/blob/main/upgrade.sh).
   - Consulta il [Manuale degli script di installazione e ciclo di vita](https://github.com/arthexis/arthexis/blob/main/docs/development/install-lifecycle-scripts-manual.md) per l'elenco completo dei flag e le note operative.
   - Consulta il [Flusso di auto-aggiornamento](https://github.com/arthexis/arthexis/blob/main/docs/auto-upgrade.md) per capire come vengono eseguiti gli upgrade delegati e come monitorarli.

- **Windows:**
   - Esegui [`install.bat`](https://github.com/arthexis/arthexis/blob/main/install.bat) per installare (ruolo Terminal) e [`upgrade.bat`](https://github.com/arthexis/arthexis/blob/main/upgrade.bat) per aggiornare.
   - Non è necessario installare per avviare in modalità Terminal (predefinita).

### 4. Amministrazione
- Accedi al Django admin su `localhost:8888/admin/` per verificare e gestire i dati in tempo reale. Usa `--port` con gli script di avvio o l'installer quando devi esporre una porta diversa.
- Consulta gli admindocs su `localhost:8888/admindocs/` per leggere la documentazione API generata automaticamente dai tuoi modelli.
- Schema dei canali di aggiornamento:

| Canale | Cadenza di controllo | Scopo | Flag di attivazione |
| --- | --- | --- | --- |
| Stable | Settimanale (giovedì prima delle 5:00) | Segue le revisioni di rilascio con controlli automatici settimanali. | `--stable` |
| Latest | Giornaliera (alla stessa ora) | Segue le revisioni più recenti della linea principale con controlli quotidiani. | `--latest` / `-l` o `--unstable` |
| Manual | Nessuna (solo aggiornamenti manuali) | Disattiva il ciclo di aggiornamento automatico per il pieno controllo operativo. Questo è il comportamento predefinito se non specifichi un canale. | _Esegui gli upgrade su richiesta senza specificare un canale._ |

- Segui la [Guida all'installazione e all'amministrazione](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/install-start-stop-upgrade-uninstall.md) per attività di deployment, ciclo di vita e runbook operativi.
- Esegui onboarding e manutenzione dei caricabatterie con il [Cookbook Connettività e Manutenzione EVCS](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/evcs-connectivity-maintenance.md).
- Configura i gateway di pagamento con il [Cookbook dei processori di pagamento](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/payment-processors.md).
- Fai riferimento al [Cookbook dei sigilli](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/sigils.md) quando configuri impostazioni basate su token tra gli ambienti.
- Comprendi fixture seed e file per utente con [Gestione dei dati locali del nodo](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/managing-local-node-data.md).
- Gestisci esportazioni, importazioni e tracciamenti con il [Cookbook sui dati utente](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/user-data.md).
- Pianifica le strategie di rilascio delle funzionalità con il [Cookbook sulle funzionalità dei nodi](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/node-features.md).
- Cura scorciatoie per gli utenti esperti tramite il [Cookbook dei preferiti](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/favorites.md).
- Collega i workspace Slack con il [Cookbook di onboarding dello Slack Bot](https://github.com/arthexis/arthexis/blob/main/apps/docs/cookbooks/slack-bot-onboarding.md).

### 5. Sviluppo
- Consulta la [Libreria della documentazione per sviluppatori](../../../docs/index.md) per riferimenti di architettura, manuali di protocollo e flussi di contribuzione.
- Nota: questo link punta alla documentazione nel repository, non a una route web di runtime.

## Supporto

Costellazione Arthexis è ancora in fase di sviluppo molto attivo e ogni giorno vengono aggiunte nuove funzionalità.

Se decidi di utilizzare la nostra suite per i tuoi progetti energetici, puoi contattarci all'indirizzo [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) o visitare la nostra [pagina web](https://www.gelectriic.com/) per servizi professionali e supporto commerciale.

## Licenza e sponsorizzazione

Arthexis è distribuito secondo la Arthexis Reciprocity General License 1.0. Oltre a codice, documentazione, revisioni e manutenzione, consideriamo anche il sostegno economico ad Arthexis e il lavoro retribuito o volontario a favore delle dipendenze open source su cui ci basiamo come una forma di contributo valida e importante.

Se Arthexis aiuta il tuo team, consulta i termini della licenza in [`LICENSE`](../../../LICENSE) e valuta la possibilità di sponsorizzare o sostenere direttamente chi mantiene le librerie, i framework e i progetti infrastrutturali che rendono possibile questa suite. Sostenere queste dipendenze aiuta a mantenere sano l'intero ecosistema Arthexis.
