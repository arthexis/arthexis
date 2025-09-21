# Constellation Arthexis

## Objectif

Constellation Arthexis est une [suite logicielle](https://fr.wikipedia.org/wiki/Suite_logicielle) [centrée sur la narration](https://fr.wikipedia.org/wiki/Narration) basée sur [Django](https://www.djangoproject.com/) qui centralise des outils pour gérer l'[infrastructure de recharge de véhicules électriques](https://fr.wikipedia.org/wiki/Infrastructure_de_charge) et orchestrer des [produits](https://fr.wikipedia.org/wiki/Produit_(%C3%A9conomie)) et [services](https://fr.wikipedia.org/wiki/Service_(%C3%A9conomie)) liés à l'[énergie](https://fr.wikipedia.org/wiki/%C3%89nergie).

## Fonctionnalités

- Compatible avec le [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/) en tant que système central, prenant en charge :
  - BootNotification
  - Heartbeat
  - StatusNotification
  - Authorize
  - MeterValues
  - DiagnosticsStatusNotification
  - StartTransaction
  - StopTransaction
  - FirmwareStatusNotification
- Intégration de [API](https://fr.wikipedia.org/wiki/Interface_de_programmation) avec [Odoo](https://www.odoo.com/) pour :
  - Synchroniser les identifiants employés via `res.users`
  - Consulter le catalogue produits via `product.product`
- Fonctionne sur [Windows 11](https://www.microsoft.com/windows/windows-11) et [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Testé pour le [Raspberry Pi 4 Modèle B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Architecture à rôles

Constellation Arthexis est déclinée en quatre rôles de nœud pour répondre à différents scénarios de déploiement.

| Rôle | Description | Fonctionnalités courantes |
| --- | --- | --- |
| Terminal | Recherche et développement monoposte | • GUI Toast |
| Control | Tests sur un appareil unique et appliances spécialisées | • AP Public Wi-Fi<br>• Celery Queue<br>• GUI Toast<br>• LCD Screen<br>• NGINX Server<br>• RFID Scanner |
| Satellite | Périphérie multi-appareils, réseau et acquisition de données | • AP Router<br>• Celery Queue<br>• NGINX Server<br>• RFID Scanner |
| Constellation | Orchestration et cloud multi-utilisateurs | • Celery Queue<br>• NGINX Server |

## Quick Guide

### 1. Cloner
- **[Linux](https://fr.wikipedia.org/wiki/Linux)** : ouvrez un [terminal](https://fr.wikipedia.org/wiki/Interface_en_ligne_de_commande) et exécutez  
  `git clone https://github.com/arthexis/arthexis.git`
- **[Windows](https://fr.wikipedia.org/wiki/Microsoft_Windows)** : ouvrez [PowerShell](https://learn.microsoft.com/fr-fr/powershell/) ou [Git Bash](https://gitforwindows.org/) et exécutez la même commande.

### 2. Démarrer et arrêter
Les nœuds Terminal peuvent démarrer directement avec les scripts ci-dessous sans installation ; les rôles Control, Satellite et Constellation doivent être installés au préalable. Les deux méthodes écoutent par défaut sur [`http://localhost:8000/`](http://localhost:8000/) ; utilisez `--port` pour choisir une autre valeur.
- **[VS Code](https://code.visualstudio.com/)** : ouvrez le dossier, rendez-vous dans le panneau **Run and Debug** (`Ctrl+Shift+D`), choisissez la configuration **Run Server** (ou **Debug Server**) et appuyez sur le bouton vert. Arrêtez le serveur avec le carré rouge (`Shift+F5`).
- **[Shell](https://fr.wikipedia.org/wiki/Interface_en_ligne_de_commande)** : sous Linux exécutez [`./start.sh`](start.sh) et arrêtez avec [`./stop.sh`](stop.sh) ; sous Windows exécutez [`start.bat`](start.bat) et arrêtez avec `Ctrl+C`.

### 3. Installer et mettre à jour
- **Linux** : exécutez [`./install.sh`](install.sh) avec un indicateur de rôle de nœud :
  - `--terminal` : rôle par défaut s'il n'est pas précisé et recommandé si vous hésitez. Les nœuds Terminal peuvent aussi utiliser les scripts ci-dessus pour démarrer/arrêter sans installation.
  - `--control` : prépare l’appliance de test monoposte.
  - `--satellite` : configure le nœud de collecte de données en périphérie.
  - `--constellation` : active la pile d’orchestration multi-utilisateurs.
  Utilisez `./install.sh --help` pour afficher la liste complète des indicateurs si vous devez personnaliser le nœud au-delà du rôle. Mettez à jour avec [`./upgrade.sh`](upgrade.sh).
- **Windows** : lancez [`install.bat`](install.bat) pour installer (rôle Terminal) et [`upgrade.bat`](upgrade.bat) pour mettre à jour.

### 4. Administration
Visitez [`http://localhost:8000/admin/`](http://localhost:8000/admin/) pour l'[administration Django](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) et [`http://localhost:8000/admindocs/`](http://localhost:8000/admindocs/) pour la [documentation d’administration](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Utilisez `--port` avec les scripts de démarrage ou l’installateur pour exposer un autre port.

## Support

Contactez-nous à [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) ou visitez notre [page web](https://www.gelectriic.com/) pour des [services professionnels](https://fr.wikipedia.org/wiki/Services_professionnels) et un [support commercial](https://fr.wikipedia.org/wiki/Support_technique).

## À propos de moi

> "Quoi, vous voulez aussi en savoir plus sur moi ? Eh bien, j'aime [développer des logiciels](https://fr.wikipedia.org/wiki/D%C3%A9veloppement_de_logiciel), les [jeux de rôle](https://fr.wikipedia.org/wiki/Jeu_de_r%C3%B4le), les longues promenades sur la [plage](https://fr.wikipedia.org/wiki/Plage) et une quatrième chose secrète."
> --Arthexis
