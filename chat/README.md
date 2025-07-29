# Chat App

This project includes basic websocket support using [Django Channels](https://channels.readthedocs.io/). After launching the development server the console now prints the available WebSocket endpoint:

```
WebSocket available at ws://localhost:8000/ws/echo/
```

You can connect a WebSocket client to this URL and any text you send will be echoed back.
