from django.contrib import admin
from .models import *


admin.site.register(Workshop)
admin.site.register(Machine)
admin.site.register(BreakDown)
admin.site.register(BreakDownMove)
admin.site.register(WorkShopParticipant)
admin.site.register(CurrentWorkshop)
admin.site.register(Department)