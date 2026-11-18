from django.contrib import admin

from .models import Family, Operation, Phase, Factory, Department, Document, DocumentVersion


@admin.register(Family, Factory, Department)
class SimpleDictionaryAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    search_fields = ['name']


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ['name', 'family', 'is_active']
    list_filter = ['family']
    search_fields = ['name']


@admin.register(Phase)
class PhaseAdmin(admin.ModelAdmin):
    list_display = ['name', 'operation', 'is_active']
    search_fields = ['name']


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    readonly_fields = ['version_number', 'created_at']


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['title', 'phase', 'factory', 'department', 'created_by', 'created_at']
    search_fields = ['title']
    inlines = [DocumentVersionInline]


from .models import Notification  # noqa: E402


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'recipient', 'kind', 'message', 'read_at']
    list_filter = ['kind']
    search_fields = ['message', 'recipient__username']
    raw_id_fields = ['document', 'version', 'step']
