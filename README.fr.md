# Constellation Arthexis

## Objectif

Constellation Arthexis est une [suite logicielle](https://fr.wikipedia.org/wiki/Suite_logicielle) [centrée sur la narration](https://fr.wikipedia.org/wiki/Narration) basée sur [Django](https://www.djangoproject.com/) qui centralise des outils pour gérer l'[infrastructure de recharge de véhicules électriques](https://fr.wikipedia.org/wiki/Infrastructure_de_charge) et orchestrer des [produits](https://fr.wikipedia.org/wiki/Produit_(%C3%A9conomie)) et [services](https://fr.wikipedia.org/wiki/Service_(%C3%A9conomie)) liés à l'[énergie](https://fr.wikipedia.org/wiki/%C3%89nergie).

## Fonctionnalités

- Compatible avec le [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/)
- Intégration de [API](https://fr.wikipedia.org/wiki/Interface_de_programmation) avec [Odoo](https://www.odoo.com/) 1.6
- Fonctionne sur [Windows 11](https://www.microsoft.com/windows/windows-11) et [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Testé pour le [Raspberry Pi 4 Modèle B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Quick Guide

### 1. Cloner
- **[Linux](https://fr.wikipedia.org/wiki/Linux)** : ouvrez un [terminal](https://fr.wikipedia.org/wiki/Interface_en_ligne_de_commande) et exécutez  
  `git clone https://github.com/arthexis/arthexis.git`
- **[Windows](https://fr.wikipedia.org/wiki/Microsoft_Windows)** : ouvrez [PowerShell](https://learn.microsoft.com/fr-fr/powershell/) ou [Git Bash](https://gitforwindows.org/) et exécutez la même commande.

### 2. Démarrer et arrêter
- **[VS Code](https://code.visualstudio.com/)** : ouvrez le dossier puis exécutez  
  `python [vscode_manage.py](vscode_manage.py) runserver` ; appuyez sur `Ctrl+C` pour arrêter.
- **[Shell](https://fr.wikipedia.org/wiki/Interface_en_ligne_de_commande)** : sous Linux exécutez [`./start.sh`](start.sh) et arrêtez avec [`./stop.sh`](stop.sh) ; sous Windows exécutez [`start.bat`](start.bat) et arrêtez avec `Ctrl+C`.

### 3. Installer et mettre à jour
- **Linux** : utilisez [`./install.sh`](install.sh) avec des options comme `--service NOM`, `--public` ou `--internal`, `--port PORT`, `--upgrade`, `--auto-upgrade`, `--latest`, `--celery`, `--lcd-screen`, `--no-lcd-screen`, `--clean`, `--datasette`. Mettez à jour avec [`./upgrade.sh`](upgrade.sh) en utilisant des options telles que `--latest`, `--clean` ou `--no-restart`.
- **Windows** : lancez [`install.bat`](install.bat) pour installer et [`upgrade.bat`](upgrade.bat) pour mettre à jour.

### 4. Administration
Visitez [`http://localhost:8888/admin/`](http://localhost:8888/admin/) pour l'[administration Django](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) et [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) pour la [documentation d’administration](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Utilisez le port `8000` si vous avez démarré avec [`start.bat`](start.bat) ou l’option `--public`.

## Support

Contactez-nous à [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) ou visitez notre [page web](https://www.gelectriic.com/) pour des [services professionnels](https://fr.wikipedia.org/wiki/Services_professionnels) et un [support commercial](https://fr.wikipedia.org/wiki/Support_technique).

## À propos de moi

> "Quoi, vous voulez aussi en savoir plus sur moi ? Eh bien, j'aime [développer des logiciels](https://fr.wikipedia.org/wiki/D%C3%A9veloppement_de_logiciel), les [jeux de rôle](https://fr.wikipedia.org/wiki/Jeu_de_r%C3%B4le), les longues promenades sur la [plage](https://fr.wikipedia.org/wiki/Plage) et une quatrième chose secrète."
> --Arthexis
