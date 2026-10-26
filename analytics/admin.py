from django.contrib import admin

from .models import AnomalyFlag, ExchangeRate, Report, SavedFilter

admin.site.register(SavedFilter)
admin.site.register(Report)
admin.site.register(AnomalyFlag)
admin.site.register(ExchangeRate)
