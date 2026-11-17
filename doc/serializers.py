import json
import os

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from .models import Family, Operation, Phase, Factory, Department, Document, DocumentVersion, ApprovalStep
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


def serialize_steps(version) -> list[dict]:
    """Kroki ścieżki akceptacji wersji (approval_steps powinny być prefetchowane)."""
    active_ids = {step.id for step in version.active_steps()}
    return [
        {
            'id': step.id,
            'order': step.order,
            'section': step.section,
            'section_label': DOC_SECTIONS.get(step.section, step.section),
            'status': step.status,
            'is_active': step.id in active_ids,
            'decided_by_name': user_display_name(step.decided_by),
            'decided_at': step.decided_at,
            'comment': step.comment,
            'attachments': [
                {
                    'id': attachment.id,
                    'original_filename': attachment.original_filename,
                    'is_image': attachment.is_image,
                    'url': f"/api/doc/step-attachments/{attachment.id}/file/",
                }
                for attachment in step.attachments.all()
            ],
        }
        for step in version.approval_steps.all()
    ]


class DocumentVersionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    approval_steps = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = [
            'id', 'version_number', 'original_filename', 'file_url', 'change_description',
            'status', 'status_display', 'uploaded_by_name', 'created_at',
            'decided_by_name', 'decided_at', 'decision_comment', 'approval_steps',
        ]

    def get_approval_steps(self, obj):
        return serialize_steps(obj)

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
    approval_path = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 'number', 'title', 'family', 'operation', 'phase', 'factory', 'department',
            'created_by_name', 'created_at', 'latest_version', 'current_version_number', 'approval_path',
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
            'approval_steps': serialize_steps(latest),
        }

    def get_approval_path(self, obj):
        """Ścieżka akceptacji dokumentu (z najnowszej wersji, która ją ma) – niezmienna przy nowych wersjach."""
        for version in self._versions(obj):
            steps = list(version.approval_steps.all())
            if steps:
                return [step.section for step in sorted(steps, key=lambda s: (s.order, s.id))]
        return []

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


class ApprovalPathField(serializers.Field):
    """
    Ścieżka akceptacji: sekcje w kolejności zatwierdzania, np. ["doc_quality", "doc_bhp"].
    Przy wysyłce multipart przychodzi jako tekst JSON.
    """
    default_error_messages = {
        'invalid': 'Nieprawidłowa ścieżka akceptacji.',
        'empty': 'Dodaj co najmniej jeden krok akceptacji.',
        'unknown': 'Nieznana sekcja: {section}.',
        'duplicate': 'Każda sekcja może wystąpić w ścieżce tylko raz.',
        'no_members': 'Sekcja {section} nie ma żadnej aktywnej osoby – dokument nie mógłby zostać zaakceptowany.',
    }

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                self.fail('invalid')
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            self.fail('invalid')
        if not data:
            self.fail('empty')
        for section in data:
            if section not in DOC_SECTIONS:
                self.fail('unknown', section=section)
        if len(set(data)) != len(data):
            self.fail('duplicate')
        for section in data:
            if not User.objects.filter(groups__name=section, is_active=True).exists():
                self.fail('no_members', section=DOC_SECTIONS[section])
        return data

    def to_representation(self, value):
        return value


def _create_version(document, file, change_description, approval_path, user) -> DocumentVersion:
    next_number = (document.versions.aggregate(m=Max('version_number'))['m'] or 0) + 1
    version = DocumentVersion.objects.create(
        document=document,
        version_number=next_number,
        file=file,
        original_filename=os.path.basename(file.name),
        change_description=change_description,
        uploaded_by=user,
    )
    ApprovalStep.objects.bulk_create([
        ApprovalStep(version=version, order=index, section=section)
        for index, section in enumerate(approval_path, start=1)
    ])
    return version


class DocumentCreateSerializer(serializers.Serializer):
    """Nowy dokument z pierwszą wersją pliku i jej ścieżką akceptacji."""
    title = serializers.CharField(max_length=255)
    phase = serializers.PrimaryKeyRelatedField(queryset=Phase.objects.filter(is_active=True))
    factory = serializers.PrimaryKeyRelatedField(queryset=Factory.objects.filter(is_active=True))
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.filter(is_active=True))
    file = serializers.FileField()
    change_description = serializers.CharField(required=False, allow_blank=True, default='')
    approval_path = ApprovalPathField()

    def create(self, validated_data):
        user = self.context['request'].user
        file = validated_data.pop('file')
        change_description = validated_data.pop('change_description')
        approval_path = validated_data.pop('approval_path')
        with transaction.atomic():
            document = Document.objects.create(created_by=user, **validated_data)
            _create_version(document, file, change_description, approval_path, user)
        return document


def document_approval_path(document) -> list[str]:
    """
    Ścieżka akceptacji dokumentu – ustalona przy pierwszej wersji ze ścieżką i niezmienna dla kolejnych.
    Pusta lista oznacza dokument sprzed wprowadzenia ścieżek (wtedy ścieżkę wybiera się raz, przy nowej wersji).
    """
    version = (
        document.versions.filter(approval_steps__isnull=False).distinct().order_by('-version_number').first()
    )
    if not version:
        return []
    return list(version.approval_steps.order_by('order', 'id').values_list('section', flat=True))


class DocumentVersionCreateSerializer(serializers.Serializer):
    """Nowa wersja istniejącego dokumentu – ścieżka akceptacji przechodzi z poprzedniej wersji bez zmian."""
    file = serializers.FileField()
    change_description = serializers.CharField(required=False, allow_blank=True, default='')
    approval_path = ApprovalPathField(required=False)

    def validate(self, attrs):
        document = self.context['document']
        _validate_has_no_pending(document)

        locked_path = document_approval_path(document)
        if locked_path:
            sent_path = attrs.get('approval_path')
            if sent_path is not None and sent_path != locked_path:
                raise serializers.ValidationError(
                    {'approval_path': 'Ścieżki akceptacji nie można zmienić przy nowej wersji dokumentu.'}
                )
            # Sekcja mogła w międzyczasie zostać bez ludzi – wtedy nikt nie zaakceptowałby wersji
            ApprovalPathField().run_validation(locked_path)
            attrs['approval_path'] = locked_path
        elif 'approval_path' not in attrs:
            raise serializers.ValidationError(
                {'approval_path': 'Ten dokument nie ma jeszcze ścieżki akceptacji – wybierz ją.'}
            )
        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            return _create_version(
                self.context['document'],
                validated_data['file'],
                validated_data['change_description'],
                validated_data['approval_path'],
                self.context['request'].user,
            )


class PendingApprovalSerializer(serializers.ModelSerializer):
    """Skrzynka akceptującego: krok, który teraz czeka na decyzję jego sekcji."""
    section_label = serializers.SerializerMethodField()
    document = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalStep
        fields = ['id', 'order', 'section', 'section_label', 'document', 'version', 'steps']

    def get_section_label(self, obj):
        return DOC_SECTIONS.get(obj.section, obj.section)

    def get_document(self, obj):
        document = obj.version.document
        phase = document.phase
        return {
            'id': document.id,
            'number': document.number,
            'title': document.title,
            'family': phase.operation.family.name,
            'operation': phase.operation.name,
            'phase': phase.name,
            'factory': document.factory.name,
            'department': document.department.name,
        }

    def get_version(self, obj):
        version = obj.version
        return {
            'id': version.id,
            'version_number': version.version_number,
            'original_filename': version.original_filename,
            'file_url': f"/api/doc/versions/{version.id}/file/",
            'change_description': version.change_description,
            'uploaded_by_name': user_display_name(version.uploaded_by),
            'created_at': version.created_at,
        }

    def get_steps(self, obj):
        return serialize_steps(obj.version)


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
