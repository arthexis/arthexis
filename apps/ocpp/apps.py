from django.apps import AppConfig


class OcppConfig(AppConfig):
    name = "apps.ocpp"

    def ready(self) -> None:
        from apps.ocpp.subscribers import register_subscribers

        register_subscribers()
