from .common_imports import *

class TransactionExportForm(forms.Form):
    start = forms.DateTimeField(required=False)
    end = forms.DateTimeField(required=False)
    chargers = forms.ModelMultipleChoiceField(
        queryset=Charger.objects.all(), required=False
    )


class TransactionImportForm(forms.Form):
    file = forms.FileField()

class MeterValueInline(admin.TabularInline):
    model = MeterValue
    extra = 0
    fields = (
        "timestamp",
        "context",
        "energy",
        "voltage",
        "current_import",
        "current_offered",
        "temperature",
        "soc",
        "connector_id",
    )
    readonly_fields = fields
    can_delete = False

class TransactionAdmin(EntityModelAdmin):
    change_list_template = "admin/ocpp/transaction/change_list.html"
    list_display = (
        "charger",
        "connector_number",
        "account",
        "rfid",
        "vid",
        "meter_start",
        "meter_stop",
        "start_time",
        "stop_time",
        "kw",
    )
    readonly_fields = ("kw", "received_start_time", "received_stop_time")
    list_filter = ("charger", "account")
    date_hierarchy = "start_time"
    inlines = [MeterValueInline]

    def connector_number(self, obj):
        return obj.connector_id or ""

    connector_number.short_description = "#"
    connector_number.admin_order_field = "connector_id"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "export/",
                self.admin_site.admin_view(self.export_view),
                name="ocpp_transaction_export",
            ),
            path(
                "import/",
                self.admin_site.admin_view(self.import_view),
                name="ocpp_transaction_import",
            ),
        ]
        return custom + urls

    def export_view(self, request):
        if request.method == "POST":
            form = TransactionExportForm(request.POST)
            if form.is_valid():
                chargers = form.cleaned_data["chargers"]
                data = export_transactions(
                    start=form.cleaned_data["start"],
                    end=form.cleaned_data["end"],
                    chargers=[c.charger_id for c in chargers] if chargers else None,
                )
                response = HttpResponse(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    content_type="application/json",
                )
                response["Content-Disposition"] = (
                    "attachment; filename=transactions.json"
                )
                return response
        else:
            form = TransactionExportForm()
        context = self.admin_site.each_context(request)
        context["form"] = form
        return TemplateResponse(request, "admin/ocpp/transaction/export.html", context)

    def import_view(self, request):
        if request.method == "POST":
            form = TransactionImportForm(request.POST, request.FILES)
            if form.is_valid():
                data = json.load(form.cleaned_data["file"])
                imported = import_transactions_data(data)
                self.message_user(request, f"Imported {imported} transactions")
                return HttpResponseRedirect("../")
        else:
            form = TransactionImportForm()
        context = self.admin_site.each_context(request)
        context["form"] = form
        return TemplateResponse(request, "admin/ocpp/transaction/import.html", context)
