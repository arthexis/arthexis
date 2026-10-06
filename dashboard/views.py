from django.shortcuts import render


def _dashboard_context():
    chargers = [
        {"name": "A01", "status": "Charging", "power": "18.6 kW", "detail": "Connector 1", "tone": "emerald"},
        {"name": "A02", "status": "Charging", "power": "19.1 kW", "detail": "Connector 1", "tone": "emerald"},
        {"name": "A03", "status": "Available", "power": "—", "detail": "Ready", "tone": "sky"},
        {"name": "B01", "status": "Offline", "power": "—", "detail": "11 min ago", "tone": "rose"},
    ]
    return {
        "site_name": "Plaza La Silla",
        "chargers": chargers,
        "summary": {
            "chargers": 12,
            "charging": 9,
            "site_power": "126 kW",
            "offline": 1,
        },
    }


def index(request):
    return render(request, "dashboard/index.html", _dashboard_context())


def status_fragment(request):
    return render(request, "dashboard/_charger_status.html", _dashboard_context())
