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
admin.site.register(ProductionTeam)
admin.site.register(CancelledBreakdownSnapshot)
admin.site.register(AdditionalEndingBreakdownInfo)
admin.site.register(WorkSchedulePreset)
admin.site.register(ScheduleBreak)
admin.site.register(MachineLineName)


class CycleChannelInline(admin.TabularInline):
    model = CycleChannel
    extra = 0
    readonly_fields = ('counter', 'last_cycle_at', 'last_num_qa', 'num_qa_checked_at')


@admin.register(CycleStation)
class CycleStationAdmin(admin.ModelAdmin):
    list_display = ('name', 'machine', 'is_active', 'last_hostname', 'last_seen_at')
    readonly_fields = ('api_key_hash', 'config_version', 'worker_alive', 'worker_paused',
                       'last_seen_at', 'last_ip', 'last_hostname')
    inlines = [CycleChannelInline]


admin.site.register(CycleStationCommand)
admin.site.register(CycleStationEvent)
