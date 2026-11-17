import os

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from .models import Family, Operation, Phase, Factory, Department, Document, DocumentVersion
from .roles import DOC_ROLES, DOC_SECTIONS

User = get_user_model()


# --- Słowniki --------------------------------------------------------------

class FamilySerializer(serializers.ModelSerializer):
    class Meta:
        model = Family
        fields = ['id', 'name', 'is_active']


class OperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Operation
        fields = ['id', 'family', 'name', 'is_active']


class PhaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Phase
        fields = ['id', 'operation', 'name', 'is_active']


class FactorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Factory
        fields = ['id', 'name', 'is_active']


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name', 'is_active']


# --- Dokumenty -------------------------------------------------------------

def user_display_name(user) -> str:
    if not user:
        return ''
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username


class DocumentVersionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = [
            'id', 'version_number', 'original_filename', 'file_url', 'change_description',
            'status', 'status_display', 'uploaded_by_name', 'created_at',
            'decided_by_name', 'decided_at', 'decision_comment',
        ]

    def get_uploaded_by_name(self, obj):
        return user_display_name(obj.uploaded_by)

    def get_decided_by_name(self, obj):
        return user_display_name(obj.decided_by)

    def get_file_url(self, obj):
        # Plik idzie przez API (kontrola dostępu + wyświetlanie w ramce na tej samej domenie)
        return f"/api/doc/versions/{obj.id}/file/"


class DocumentListSerializer(serializers.ModelSerializer):
    """Lista dokumentów dodającego / administratora."""
    number = serializers.CharField(read_only=True)
    family = serializers.SerializerMethodField()
    operation = serializers.SerializerMethodField()
    phase = serializers.SerializerMethodField()
    factory = serializers.CharField(source='factory.name')
    department = serializers.CharField(source='department.name')
    created_by_name = serializers.SerializerMethodField()
    latest_version = serializers.SerializerMethodField()
    current_version_number = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 'number', 'title', 'family', 'operation', 'phase', 'factory', 'department',
            'created_by_name', 'created_at', 'latest_version', 'current_version_number',
        ]

    def get_family(self, obj):
        family = obj.phase.operation.family
        return {'id': family.id, 'name': family.name}

    def get_operation(self, obj):
        operation = obj.phase.operation
        return {'id': operation.id, 'name': operation.name}

    def get_phase(self, obj):
        return {'id': obj.phase.id, 'name': obj.phase.name}

    def get_created_by_name(self, obj):
        return user_display_name(obj.created_by)

    def _versions(self, obj):
        # versions są prefetchowane, posortowane malejąco po numerze
        return list(obj.versions.all())

    def get_latest_version(self, obj):
        versions = self._versions(obj)
        if not versions:
            return None
        latest = versions[0]
        return {
            'id': latest.id,
            'version_number': latest.version_number,
            'status': latest.status,
            'status_display': latest.get_status_display(),
            'created_at': latest.created_at,
        }

    def get_current_version_number(self, obj):
        approved = [v for v in self._versions(obj) if v.status == DocumentVersion.Status.APPROVED]
        return approved[0].version_number if approved else None


class DocumentDetailSerializer(DocumentListSerializer):
    versions = DocumentVersionSerializer(many=True, read_only=True)
    factory_id = serializers.IntegerField(source='factory.id', read_only=True)
    department_id = serializers.IntegerField(source='department.id', read_only=True)

    class Meta(DocumentListSerializer.Meta):
        fields = DocumentListSerializer.Meta.fields + ['factory_id', 'department_id', 'versions']


def _validate_has_no_pending(document):
    if document.versions.filter(status=DocumentVersion.Status.PENDING).exists():
        raise serializers.ValidationError(
            {'detail': 'Poprzednia wersja tego dokumentu czeka jeszcze na akceptację.'}
        )


def _create_version(document, file, change_description, user) -> DocumentVersion:
    next_number = (document.versions.aggregate(m=Max('version_number'))['m'] or 0) + 1
    return DocumentVersion.objects.create(
        document=document,
        version_number=next_number,
        file=file,
        original_filename=os.path.basename(file.name),
        change_description=change_description,
        uploaded_by=user,
    )


class DocumentCreateSerializer(serializers.Serializer):
    """Nowy dokument z pierwszą wersją pliku."""
    title = serializers.CharField(max_length=255)
    phase = serializers.PrimaryKeyRelatedField(queryset=Phase.objects.filter(is_active=True))
    factory = serializers.PrimaryKeyRelatedField(queryset=Factory.objects.filter(is_active=True))
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.filter(is_active=True))
    file = serializers.FileField()
    change_description = serializers.CharField(required=False, allow_blank=True, default='')

    def create(self, validated_data):
        user = self.context['request'].user
        file = validated_data.pop('file')
        change_description = validated_data.pop('change_description')
        with transaction.atomic():
            document = Document.objects.create(created_by=user, **validated_data)
            _create_version(document, file, change_description, user)
        return document


class DocumentVersionCreateSerializer(serializers.Serializer):
    """Nowa wersja istniejącego dokumentu."""
    file = serializers.FileField()
    change_description = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        _validate_has_no_pending(self.context['document'])
        return attrs

    def create(self, validated_data):
        return _create_version(
            self.context['document'],
            validated_data['file'],
            validated_data['change_description'],
            self.context['request'].user,
        )


class OperatorDocumentSerializer(serializers.ModelSerializer):
    """Widok operatora: dokument z jego aktualną (najnowszą zatwierdzoną) wersją."""
    number = serializers.CharField(read_only=True)
    current_version = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ['id', 'number', 'title', 'current_version']

    def get_current_version(self, obj):
        version = obj.current_versions[0]
        return {
            'id': version.id,
            'version_number': version.version_number,
            'original_filename': version.original_filename,
            'file_url': f"/api/doc/versions/{version.id}/file/",
            'approved_at': version.decided_at,
        }


# --- Użytkownicy (panel administratora DOC) --------------------------------

class DocUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 'card_code',
            'is_active', 'is_in_change_password', 'roles', 'sections',
        ]

    def _doc_groups(self, obj):
        return {group.name for group in obj.groups.all() if group.name.startswith('doc_')}

    def get_roles(self, obj):
        return sorted(self._doc_groups(obj) & set(DOC_ROLES))

    def get_sections(self, obj):
        return sorted(self._doc_groups(obj) & set(DOC_SECTIONS))


class DocUserGroupsSerializer(serializers.Serializer):
    roles = serializers.ListField(child=serializers.ChoiceField(choices=list(DOC_ROLES)), required=False)
    sections = serializers.ListField(child=serializers.ChoiceField(choices=list(DOC_SECTIONS)), required=False)


class DocUserCreateSerializer(DocUserGroupsSerializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    card_code = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)

    def validate_card_code(self, value):
        if value and User.objects.filter(card_code=value).exists():
            raise serializers.ValidationError('Ten kod karty jest już przypisany do innego użytkownika.')
        return value or None
