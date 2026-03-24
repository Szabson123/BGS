from django.contrib import admin
from .models import *


admin.site.register(CustomUser)
admin.site.register(MaintenancePermissions)
admin.site.register(MaintenanceRole)
# Register your models here.
