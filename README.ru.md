# Созвездие Arthexis

## Назначение

Созвездие Arthexis — это [основанный на повествовании](https://ru.wikipedia.org/wiki/%D0%9D%D0%B0%D1%80%D1%80%D0%B0%D1%82%D0%B8%D0%B2) [пакет программного обеспечения](https://ru.wikipedia.org/wiki/%D0%9F%D0%B0%D0%BA%D0%B5%D1%82_%D0%BF%D1%80%D0%BE%D0%B3%D1%80%D0%B0%D0%BC%D0%BC) на основе [Django](https://www.djangoproject.com/), который централизует инструменты для управления [инфраструктурой зарядки электромобилей](https://ru.wikipedia.org/wiki/%D0%97%D0%B0%D1%80%D1%8F%D0%B4%D0%BD%D0%B0%D1%8F_%D1%81%D1%82%D0%B0%D0%BD%D1%86%D0%B8%D1%8F) и оркестровки [продуктов](https://ru.wikipedia.org/wiki/%D0%A2%D0%BE%D0%B2%D0%B0%D1%80) и [услуг](https://ru.wikipedia.org/wiki/%D0%A3%D1%81%D0%BB%D1%83%D0%B3%D0%B0), связанных с [энергией](https://ru.wikipedia.org/wiki/%D0%AD%D0%BD%D0%B5%D1%80%D0%B3%D0%B8%D1%8F).

## Возможности

- Совместим с [Open Charge Point Protocol (OCPP) 1.6](https://www.openchargealliance.org/protocols/ocpp-16/)
- [API](https://ru.wikipedia.org/wiki/API) интеграция с [Odoo](https://www.odoo.com/) 1.6
- Работает на [Windows 11](https://www.microsoft.com/windows/windows-11) и [Ubuntu 22.04 LTS](https://releases.ubuntu.com/22.04/)
- Протестирован на [Raspberry Pi 4 Model B](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/)

## Архитектура из ролей

Созвездие Arthexis поставляется в четырёх ролях узлов, чтобы адаптировать платформу к различным сценариям развёртывания.

| Роль | Описание | Типичные функции |
| --- | --- | --- |
| Terminal | Исследования и разработка для одного пользователя | • GUI Toast |
| Control | Тестирование отдельных устройств и специализированные аппаратные комплексы | • AP Public Wi-Fi<br>• Celery Queue<br>• GUI Toast<br>• LCD Screen<br>• NGINX Server<br>• RFID Scanner |
| Satellite | Периферийная многоприборная инфраструктура, сеть и сбор данных | • AP Router<br>• Celery Queue<br>• NGINX Server<br>• RFID Scanner |
| Constellation | Многопользовательское облако и оркестрация | • Celery Queue<br>• NGINX Server |

## Quick Guide

### 1. Клонирование
- **[Linux](https://ru.wikipedia.org/wiki/Linux)**: откройте [терминал](https://ru.wikipedia.org/wiki/Командная_оболочка) и выполните  
  `git clone https://github.com/arthexis/arthexis.git`
- **[Windows](https://ru.wikipedia.org/wiki/Microsoft_Windows)**: откройте [PowerShell](https://learn.microsoft.com/ru-ru/powershell/) или [Git Bash](https://gitforwindows.org/) и выполните ту же команду.

### 2. Запуск и остановка
- **[VS Code](https://code.visualstudio.com/)**: откройте папку и выполните  
  `python [vscode_manage.py](vscode_manage.py) runserver`; для остановки нажмите `Ctrl+C`.
- **[Shell](https://ru.wikipedia.org/wiki/Командная_оболочка)**: в Linux запустите [`./start.sh`](start.sh) и остановите [`./stop.sh`](stop.sh); в Windows запустите [`start.bat`](start.bat) и остановите `Ctrl+C`.

### 3. Установка и обновление
- **Linux**: используйте [`./install.sh`](install.sh) с опциями `--service ИМЯ`, `--public` или `--internal`, `--port ПОРТ`, `--upgrade`, `--auto-upgrade`, `--latest`, `--celery`, `--lcd-screen`, `--no-lcd-screen`, `--clean`, `--datasette`. Обновляйте через [`./upgrade.sh`](upgrade.sh), применяя `--latest`, `--clean` или `--no-restart`.
- **Windows**: выполните [`install.bat`](install.bat) для установки и [`upgrade.bat`](upgrade.bat) для обновления.

### 4. Администрирование
Перейдите на [`http://localhost:8888/admin/`](http://localhost:8888/admin/) для [панели администратора Django](https://docs.djangoproject.com/en/stable/ref/contrib/admin/) и [`http://localhost:8888/admindocs/`](http://localhost:8888/admindocs/) для [административной документации](https://docs.djangoproject.com/en/stable/ref/contrib/admin/admindocs/). Используйте порт `8000`, если запуск был через [`start.bat`](start.bat) или с опцией `--public`.

## Поддержка

Свяжитесь с нами по адресу [tecnologia@gelectriic.com](mailto:tecnologia@gelectriic.com) или посетите нашу [веб-страницу](https://www.gelectriic.com/) для [профессиональных услуг](https://ru.wikipedia.org/wiki/%D0%9F%D1%80%D0%BE%D1%84%D0%B5%D1%81%D1%81%D0%B8%D0%BE%D0%BD%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D0%B5_%D1%83%D1%81%D0%BB%D1%83%D0%B3%D0%B8) и [коммерческой поддержки](https://ru.wikipedia.org/wiki/%D0%A2%D0%B5%D1%85%D0%BD%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B0%D1%8F_%D0%BF%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6%D0%BA%D0%B0).

## Обо мне

> «Что, вы тоже хотите знать обо мне? Ну, я люблю [разрабатывать программное обеспечение](https://ru.wikipedia.org/wiki/%D0%A0%D0%B0%D0%B7%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D0%BA%D0%B0_%D0%BF%D1%80%D0%BE%D0%B3%D1%80%D0%B0%D0%BC%D0%BC%D0%BD%D0%BE%D0%B3%D0%BE_%D0%BE%D0%B1%D0%B5%D1%81%D0%BF%D0%B5%D1%87%D0%B5%D0%BD%D0%B8%D1%8F), [ролевые игры](https://ru.wikipedia.org/wiki/%D0%A0%D0%BE%D0%BB%D0%B5%D0%B2%D0%B0%D1%8F_%D0%B8%D0%B3%D1%80%D0%B0), длинные прогулки по [пляжу](https://ru.wikipedia.org/wiki/%D0%9F%D0%BB%D1%8F%D0%B6) и одну четвертую секретную вещь.»
> --Arthexis
