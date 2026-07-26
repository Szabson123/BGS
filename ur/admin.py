from django.contrib import admin
from .models import *


admin.site.register(Workshop)
admin.site.register(Machine)
admin.site.register(Breakdown)
admin.site.register(BreakdownMove)
admin.site.register(WorkshopParticipant)
admin.site.register(CurrentWorkshop)
admin.site.register(Department)
admin.site.register(CurrentDepartment)
admin.site.register(AdditionalEndingBreakdownInfo)