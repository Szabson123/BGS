import json
import os

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from rest_framework import serializers

from .models import Family, Operation, Phase, Factory, Department, Document, DocumentVersion, ApprovalStep, Notification
from .roles import (
    DOC_ROLES, DOC_SECTIONS, DOC_MANAGERS, SECTION_MANAGERS, is_managed, managed_sections, member_sections, section_managers, section_people,
)
from .notifications import notify_active_steps
from .conversion import can_retry, needs_conversion, request_conversion

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
            'is_managed': is_managed(step.section),
            'assigned_to_name': user_display_name(step.assigned_to),
            'assigned_at': step.assigned_at,
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


def version_pdf_url(version):
    """PDF z konwertera – tylko gdy konwersja się udała."""
    if version.conversion_status == DocumentVersion.ConversionStatus.DONE and version.pdf_file:
        return f"/api/doc/versions/{version.id}/file/?variant=pdf"
    return None


class DocumentVersionSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    approval_steps = serializers.SerializerMethodField()
    pdf_url = serializers.SerializerMethodField()
    conversion_status_display = serializers.CharField(source='get_conversion_status_display', read_only=True)
    can_retry_conversion = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = [
            'id', 'version_number', 'original_filename', 'file_url', 'change_description',
            'status', 'status_display', 'uploaded_by_name', 'created_at',
            'decided_by_name', 'decided_at', 'decision_comment', 'approval_steps',
            'pdf_url', 'conversion_status', 'conversion_status_display', 'conversion_error', 'can_retry_conversion',
        ]

    def get_pdf_url(self, obj):
        return version_pdf_url(obj)

    def get_can_retry_conversion(self, obj):
        return can_retry(obj)

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
        'no_manager': 'Sekcja {section} nie ma aktywnego managera – nikt nie przypisałby osoby do akceptacji.',
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
            if is_managed(section):
                if not section_managers(section).exists():
                    self.fail('no_manager', section=DOC_SECTIONS[section])
            elif not User.objects.filter(groups__name=section, is_active=True).exists():
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
    if needs_conversion(version.original_filename):
        request_conversion(version)
    notify_active_steps(version, actor=user)
    return version


def _find_or_new(model, name, label, **parent):
    """Istniejąca pozycja o tej nazwie (bez względu na wielkość liter) albo None – wtedy powstanie przy zapisie."""
    existing = model.objects.filter(name__iexact=name, **parent).first() if all(parent.values()) or not parent else None
    if existing and not existing.is_active:
        raise serializers.ValidationError({f'{label}_name': f'„{existing.name}” jest dezaktywowana – wybierz inną lub poproś administratora.'})
    return existing


class DocumentCreateSerializer(serializers.Serializer):
    """
    Nowy dokument z pierwszą wersją pliku i jej ścieżką akceptacji.

    Miejsce: `phase` (id istniejącej fazy) albo rodzina/operacja/faza podane jako id lub wpisana nazwa
    (`family`/`family_name`, `operation`/`operation_name`, `phase_name`). Brakujące pozycje
    tworzą się razem z dokumentem, w jednej transakcji.
    """
    title = serializers.CharField(max_length=255)
    phase = serializers.PrimaryKeyRelatedField(queryset=Phase.objects.filter(is_active=True), required=False)
    family = serializers.PrimaryKeyRelatedField(queryset=Family.objects.filter(is_active=True), required=False)
    operation = serializers.PrimaryKeyRelatedField(queryset=Operation.objects.filter(is_active=True), required=False)
    family_name = serializers.CharField(max_length=255, required=False, allow_blank=True, trim_whitespace=True)
    operation_name = serializers.CharField(max_length=255, required=False, allow_blank=True, trim_whitespace=True)
    phase_name = serializers.CharField(max_length=255, required=False, allow_blank=True, trim_whitespace=True)
    factory = serializers.PrimaryKeyRelatedField(queryset=Factory.objects.filter(is_active=True))
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.filter(is_active=True))
    file = serializers.FileField()
    change_description = serializers.CharField(required=False, allow_blank=True, default='')
    approval_path = ApprovalPathField()

    def validate(self, attrs):
        names = {key: attrs.pop(f'{key}_name', '') for key in ('family', 'operation', 'phase')}
        family, operation = attrs.pop('family', None), attrs.pop('operation', None)
        if attrs.get('phase'):
            return attrs

        if not family:
            if not names['family']:
                raise serializers.ValidationError({'family_name': 'Wybierz lub wpisz rodzinę.'})
            family = _find_or_new(Family, names['family'], 'family')
        if operation and family and operation.family_id != family.id:
            raise serializers.ValidationError({'operation': 'Operacja nie należy do wybranej rodziny.'})
        if not operation:
            if not names['operation']:
                raise serializers.ValidationError({'operation_name': 'Wybierz lub wpisz operację.'})
            operation = _find_or_new(Operation, names['operation'], 'operation', family=family)
        if not names['phase']:
            raise serializers.ValidationError({'phase_name': 'Wybierz lub wpisz fazę.'})
        phase = _find_or_new(Phase, names['phase'], 'phase', operation=operation)

        if phase:
            attrs['phase'] = phase
        else:
            attrs['_new_place'] = {'family': family, 'operation': operation, 'names': names}
        return attrs

    @staticmethod
    def _create_place(place):
        family = place['family'] or Family.objects.create(name=place['names']['family'])
        operation = place['operation'] or Operation.objects.create(family=family, name=place['names']['operation'])
        return Phase.objects.create(operation=operation, name=place['names']['phase'])

    def create(self, validated_data):
        user = self.context['request'].user
        file = validated_data.pop('file')
        change_description = validated_data.pop('change_description')
        approval_path = validated_data.pop('approval_path')
        new_place = validated_data.pop('_new_place', None)
        with transaction.atomic():
            if new_place:
                validated_data['phase'] = self._create_place(new_place)
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
    # Przy nowej wersji opis zmian jest obowiązkowy (pierwsza wersja dokumentu go nie wymaga)
    change_description = serializers.CharField(error_messages={
        'required': 'Opisz, co zmieniło się w tej wersji.',
        'blank': 'Opisz, co zmieniło się w tej wersji.',
    })
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


def can_assign_step(user, step) -> bool:
    """Wskazać osobę może manager sekcji kroku (tylko w sekcjach z managerem)."""
    return is_managed(step.section) and step.section in managed_sections(user)


def can_decide_step(user, step) -> bool:
    """
    Sekcja z managerem: wskazana osoba albo manager sekcji.
    Sekcja bez managera: dowolna osoba z sekcji.
    """
    if is_managed(step.section):
        return step.assigned_to_id == user.id or step.section in managed_sections(user)
    return step.section in member_sections(user)


class PendingApprovalSerializer(serializers.ModelSerializer):
    """
    Skrzynka akceptującego: krok, który teraz czeka na decyzję – do przypisania (manager sekcji),
    przypisany do użytkownika albo czekający na dowolną osobę z sekcji bez managera.
    """
    section_label = serializers.SerializerMethodField()
    document = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()
    is_managed = serializers.SerializerMethodField()
    assigned_to = serializers.SerializerMethodField()
    can_assign = serializers.SerializerMethodField()
    can_decide = serializers.SerializerMethodField()
    assignees = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalStep
        fields = [
            'id', 'order', 'section', 'section_label', 'document', 'version', 'steps',
            'is_managed', 'assigned_to', 'assigned_at', 'can_assign', 'can_decide', 'assignees',
        ]

    def _user(self):
        request = self.context.get('request')
        return request.user if request else None

    def get_is_managed(self, obj):
        return is_managed(obj.section)

    def get_assigned_to(self, obj):
        if not obj.assigned_to:
            return None
        return {'id': obj.assigned_to.id, 'name': user_display_name(obj.assigned_to)}

    def get_can_assign(self, obj):
        user = self._user()
        return bool(user and can_assign_step(user, obj))

    def get_can_decide(self, obj):
        user = self._user()
        return bool(user and can_decide_step(user, obj))

    def get_assignees(self, obj):
        """Osoby do wyboru przy przypisaniu – tylko dla managera sekcji."""
        user = self._user()
        if not user or not can_assign_step(user, obj):
            return []
        cache = self.context.setdefault('_section_people', {})
        if obj.section not in cache:
            people = section_people(obj.section).prefetch_related('groups').order_by('last_name', 'first_name', 'username')
            manager_group = SECTION_MANAGERS[obj.section]
            cache[obj.section] = [
                {
                    'id': person.id,
                    'name': user_display_name(person),
                    'is_manager': any(group.name == manager_group for group in person.groups.all()),
                }
                for person in people
            ]
        return cache[obj.section]

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
            'pdf_url': version_pdf_url(version),
            'change_description': version.change_description,
            'uploaded_by_name': user_display_name(version.uploaded_by),
            'created_at': version.created_at,
        }

    def get_steps(self, obj):
        return serialize_steps(obj.version)


class OperatorDocumentSerializer(serializers.ModelSerializer):
    """Panel stanowiska (publiczny): dokument z jego aktualną (najnowszą zatwierdzoną) wersją."""
    number = serializers.CharField(read_only=True)
    current_version = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = ['id', 'number', 'title', 'current_version']

    def get_current_version(self, obj):
        # Adresy publiczne (panel stanowiska działa bez logowania)
        version = obj.current_versions[0]
        base = f"/api/doc/operator/versions/{version.id}/file/"
        return {
            'id': version.id,
            'version_number': version.version_number,
            'original_filename': version.original_filename,
            'file_url': base,
            'pdf_url': f"{base}?variant=pdf" if version_pdf_url(version) else None,
            'approved_at': version.decided_at,
        }


# --- Użytkownicy (panel administratora DOC) --------------------------------

class DocUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    sections = serializers.SerializerMethodField()
    managers = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name', 'email', 'card_code',
            'is_active', 'is_in_change_password', 'roles', 'sections', 'managers',
        ]

    def _doc_groups(self, obj):
        return {group.name for group in obj.groups.all() if group.name.startswith('doc_')}

    def get_roles(self, obj):
        return sorted(self._doc_groups(obj) & set(DOC_ROLES))

    def get_sections(self, obj):
        return sorted(self._doc_groups(obj) & set(DOC_SECTIONS))

    def get_managers(self, obj):
        return sorted(self._doc_groups(obj) & set(DOC_MANAGERS))


class DocUserGroupsSerializer(serializers.Serializer):
    roles = serializers.ListField(child=serializers.ChoiceField(choices=list(DOC_ROLES)), required=False)
    sections = serializers.ListField(child=serializers.ChoiceField(choices=list(DOC_SECTIONS)), required=False)
    managers = serializers.ListField(child=serializers.ChoiceField(choices=list(DOC_MANAGERS)), required=False)


class DocUserCreateSerializer(DocUserGroupsSerializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    card_code = serializers.CharField(required=False, allow_blank=True, allow_null=True, default=None)

    def validate_card_code(self, value):
        if value and User.objects.filter(card_code=value).exists():
            raise serializers.ValidationError('Ten kod karty jest już przypisany do innego użytkownika.')
        return value or None


# --- Powiadomienia ---------------------------------------------------------

class NotificationSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    actor_name = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ['id', 'kind', 'kind_display', 'message', 'actor_name', 'link', 'is_read', 'created_at']

    def get_actor_name(self, obj):
        return user_display_name(obj.actor)

    def get_is_read(self, obj):
        return obj.read_at is not None

    def get_link(self, obj):
        """Dokąd prowadzi kliknięcie: kroki – skrzynka akceptacji, wynik – karta dokumentu."""
        if obj.kind in (Notification.Kind.APPROVED, Notification.Kind.REJECTED):
            return f"/doc/documents/{obj.document_id}"
        return "/doc/approvals"
