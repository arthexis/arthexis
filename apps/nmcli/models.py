from django.db import models
from django.utils.translation import gettext_lazy as _


class NetworkConnection(models.Model):
    connection_id = models.CharField(max_length=255, unique=True)
    uuid = models.CharField(max_length=255, unique=True, null=True, blank=True)
    connection_type = models.CharField(max_length=100, blank=True, default="")
    interface_name = models.CharField(max_length=100, blank=True, default="")
    autoconnect = models.BooleanField(default=False)
    priority = models.IntegerField(null=True, blank=True)
    metered = models.CharField(max_length=50, blank=True, default="")
    ip4_address = models.CharField(max_length=255, blank=True, default="")
    ip4_method = models.CharField(max_length=50, blank=True, default="")
    ip4_gateway = models.CharField(max_length=255, blank=True, default="")
    ip4_dns = models.CharField(max_length=255, blank=True, default="")
    ip6_address = models.CharField(max_length=255, blank=True, default="")
    ip6_method = models.CharField(max_length=50, blank=True, default="")
    ip6_gateway = models.CharField(max_length=255, blank=True, default="")
    ip6_dns = models.CharField(max_length=255, blank=True, default="")
    dhcp_client_id = models.CharField(max_length=255, blank=True, default="")
    dhcp_hostname = models.CharField(max_length=255, blank=True, default="")
    wireless_ssid = models.CharField(max_length=255, blank=True, default="")
    wireless_mode = models.CharField(max_length=50, blank=True, default="")
    wireless_band = models.CharField(max_length=50, blank=True, default="")
    wireless_channel = models.CharField(max_length=50, blank=True, default="")
    security_type = models.CharField(max_length=100, blank=True, default="")
    password = models.CharField(max_length=255, blank=True, default="")
    mac_address = models.CharField(max_length=255, blank=True, default="")
    last_nmcli_check = models.DateTimeField(null=True, blank=True)
    last_modified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("connection_id",)
        verbose_name = _("Network Connection")
        verbose_name_plural = _("Network Connections")

    def __str__(self):
        return self.connection_id or self.uuid or str(self.pk)
