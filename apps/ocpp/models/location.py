from apps.maps.models import GoogleMapsLocation as MapsGoogleMapsLocation
from apps.maps.models import Location as MapsLocation


class Location(MapsLocation):
    class Meta:
        proxy = True
        app_label = "ocpp"


class GoogleMapsLocation(MapsGoogleMapsLocation):
    class Meta:
        proxy = True
        app_label = "ocpp"
