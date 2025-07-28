# Arthexis Django Project

This repository contains a basic [Django](https://www.djangoproject.com/) project.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Apply database migrations:
   ```bash
   python manage.py migrate
   ```
3. Run the development server:
   ```bash
   python manage.py runserver
   ```

The default configuration uses SQLite and is for local development only.

## VS Code

Launch configurations are provided in `.vscode/launch.json`:

1. **Run Django Server** – starts the site normally without the debugger.
2. **Debug Django Server** – runs the server with debugging enabled.

Open the *Run and Debug* pane in VS Code and choose the desired configuration.

### Websocket example

This project includes basic websocket support using
[Django Channels](https://channels.readthedocs.io/). After launching the
development server you can connect a websocket client to
`ws://localhost:8000/ws/echo/` and any text you send will be echoed back.
