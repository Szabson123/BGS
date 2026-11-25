import json
import os

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Family, Operation, Phase, Factory, Department, Document, DocumentVersion, ApprovalStep, Notification, ChangeType,
    StepAssignmentLog,
)
from .roles import (
    DOC_ROLES, DOC_SECTIONS, DOC_MANAGERS, is_managed, is_doc_admin, managed_sections, member_sections,
    section_people,
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


def validate_section_list(data, allow_empty=False) -> list[str]:
    """Lista sekcji w kolejności akceptacji: znane klucze, bez powtórzeń."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise serializers.ValidationError('Nieprawidłowa ścieżka akceptacji.')
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise serializers.ValidationError('Nieprawidłowa ścieżka akceptacji.')
    if not data and not allow_empty:
        raise serializers.ValidationError('Dodaj co najmniej jeden krok akceptacji.')
    unknown = [section for section in data if section not in DOC_SECTIONS]
    if unknown:
        raise serializers.ValidationError(f'Nieznana sekcja: {unknown[0]}.')
    if len(set(data)) != len(data):
        raise serializers.ValidationError('Każda sekcja może wystąpić w ścieżce tylko raz.')
    return data


class ChangeTypeSerializer(serializers.ModelSerializer):
    """Typ zmiany nowej wersji (panel administratora) – sekcje akceptujące w kolejności."""
    section_labels = serializers.SerializerMethodField()
    is_used = serializers.SerializerMethodField()

    class Meta:
        model = ChangeType
        fields = ['id', 'name', 'description', 'sections', 'section_labels', 'order', 'is_active', 'is_used']

    def get_section_labels(self, obj):
        return [DOC_SECTIONS.get(section, section) for section in obj.sections]

    def get_is_used(self, obj):
        return obj.versions.exists()

    def validate_sections(self, value):
        return validate_section_list(value)


# --- Dokumenty -------------------------------------------------------------

def user_display_name(user) -> str:
    if not user:
        return ''
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username


def serialize_assignment_log(step) -> list[dict]:
    return [
        {
            'id': entry.id,
            'from_name': user_display_name(entry.from_user),
            'to_name': user_display_name(entry.to_user),
            'by_name': user_display_name(entry.by_user),
            'reason': entry.reason,
            'reason_display': entry.get_reason_display(),
            'comment': entry.comment,
            'created_at': entry.created_at,
        }
        for entry in step.assignment_log.all()
    ]


def serialize_steps(version, user=None) -> list[dict]:
    """Kroki ścieżki akceptacji wersji (approval_steps powinny być prefetchowane). `user` – do flagi can_reassign."""
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
            'assigned_to_id': step.assigned_to_id,
            'assigned_to_name': user_display_name(step.assigned_to),
            'assigned_at': step.assigned_at,
            'can_reassign': bool(user and can_reassign_step(user, step, version)),
            'assignment_log': serialize_assignment_log(step),
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
    change_type = serializers.SerializerMethodField()

    class Meta:
        model = DocumentVersion
        fields = [
            'id', 'version_number', 'original_filename', 'file_url', 'change_description', 'change_type',
            'status', 'status_display', 'uploaded_by_name', 'created_at',
            'decided_by_name', 'decided_at', 'decision_comment', 'approval_steps',
            'pdf_url', 'conversion_status', 'conversion_status_display', 'conversion_error', 'can_retry_conversion',
        ]

    def get_pdf_url(self, obj):
        return version_pdf_url(obj)

    def get_can_retry_conversion(self, obj):
        return can_retry(obj)

    def get_approval_steps(self, obj):
        request = self.context.get('request')
        return serialize_steps(obj, request.user if request else None)

    def get_change_type(self, obj):
        return {'id': obj.change_type.id, 'name': obj.change_type.name} if obj.change_type else None

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
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            'id', 'number', 'title', 'family', 'operation', 'phase', 'factory', 'department',
            'created_by_name', 'created_at', 'latest_version', 'current_version_number', 'approval_path', 'can_edit',
        ]

    def _request_user(self):
        request = self.context.get('request')
        return request.user if request else None

    def get_can_edit(self, obj):
        """Nowe wersje dodaje twórca dokumentu albo administrator; cudze instrukcje są tylko do podglądu."""
        user = self._request_user()
        return bool(user and (obj.created_by_id == user.id or is_doc_admin(user)))

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
            'change_type': {'id': latest.change_type.id, 'name': latest.change_type.name} if latest.change_type else None,
            'approval_steps': serialize_steps(latest, self._request_user()),
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
        data = validate_section_list(data)
        for section in data:
            if not section_people(section).exists():
                self.fail('no_members', section=DOC_SECTIONS[section])
        return data

    def to_representation(self, value):
        return value


class AssigneesField(serializers.Field):
    """
    Osoby wybrane przez twórcę dla sekcji z managerem: {"doc_quality": <id użytkownika>, ...}.
    Przy wysyłce multipart przychodzi jako tekst JSON.
    """
    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data or '{}')
            except ValueError:
                raise serializers.ValidationError('Nieprawidłowa lista osób.')
        if not isinstance(data, dict):
            raise serializers.ValidationError('Nieprawidłowa lista osób.')
        try:
            return {str(section): int(user_id) for section, user_id in data.items() if user_id not in (None, '')}
        except (TypeError, ValueError):
            raise serializers.ValidationError('Nieprawidłowa lista osób.')

    def to_representation(self, value):
        return value


def resolve_assignees(path: list[str], assignees: dict) -> dict:
    """Każda sekcja z managerem w ścieżce musi mieć wybraną aktywną osobę z tej sekcji."""
    errors, resolved = [], {}
    for section in path:
        if not is_managed(section):
            continue
        user_id = assignees.get(section)
        person = section_people(section).filter(pk=user_id).first() if user_id else None
        if not person:
            errors.append(f'Wybierz osobę z sekcji {DOC_SECTIONS[section]}, która zaakceptuje instrukcję.')
        else:
            resolved[section] = person
    if errors:
        raise serializers.ValidationError({'assignees': errors})
    return resolved


def _create_version(document, file, change_description, approval_path, user, assignees=None,
                    change_type=None) -> DocumentVersion:
    next_number = (document.versions.aggregate(m=Max('version_number'))['m'] or 0) + 1
    version = DocumentVersion.objects.create(
        document=document,
        version_number=next_number,
        file=file,
        original_filename=os.path.basename(file.name),
        change_description=change_description,
        change_type=change_type,
        uploaded_by=user,
    )
    assignees = assignees or {}
    now = timezone.now()
    steps = ApprovalStep.objects.bulk_create([
        ApprovalStep(
            version=version, order=index, section=section, assigned_to=assignees.get(section),
            assigned_by=user if section in assignees else None, assigned_at=now if section in assignees else None,
        )
        for index, section in enumerate(approval_path, start=1)
    ])
    StepAssignmentLog.objects.bulk_create([
        StepAssignmentLog(step=step, to_user=step.assigned_to, by_user=user, reason=StepAssignmentLog.Reason.CREATED)
        for step in steps if step.assigned_to
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
    assignees = AssigneesField(required=False, default=dict)

    def validate(self, attrs):
        attrs['assignees'] = resolve_assignees(attrs['approval_path'], attrs.get('assignees') or {})
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
        assignees = validated_data.pop('assignees')
        new_place = validated_data.pop('_new_place', None)
        with transaction.atomic():
            if new_place:
                validated_data['phase'] = self._create_place(new_place)
            document = Document.objects.create(created_by=user, **validated_data)
            _create_version(document, file, change_description, approval_path, user, assignees)
        return document


class DocumentVersionCreateSerializer(serializers.Serializer):
    """
    Nowa wersja istniejącego dokumentu. Typ zmiany (ustala administrator) wyznacza sekcje akceptujące,
    a twórca wybiera osoby dla sekcji z managerem.
    """
    file = serializers.FileField()
    # Przy nowej wersji opis zmian jest obowiązkowy (pierwsza wersja dokumentu go nie wymaga)
    change_description = serializers.CharField(error_messages={
        'required': 'Opisz, co zmieniło się w tej wersji.',
        'blank': 'Opisz, co zmieniło się w tej wersji.',
    })
    change_type = serializers.PrimaryKeyRelatedField(
        queryset=ChangeType.objects.filter(is_active=True),
        error_messages={'required': 'Wybierz typ zmiany.', 'null': 'Wybierz typ zmiany.',
                        'does_not_exist': 'Ten typ zmiany nie jest już dostępny.'},
    )
    assignees = AssigneesField(required=False, default=dict)

    def validate(self, attrs):
        _validate_has_no_pending(self.context['document'])
        change_type = attrs['change_type']
        try:
            # Sekcja z typu mogła w międzyczasie zostać bez ludzi – wtedy nikt nie zaakceptowałby wersji
            path = ApprovalPathField().run_validation(change_type.sections)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'change_type': exc.detail})
        attrs['approval_path'] = path
        attrs['assignees'] = resolve_assignees(path, attrs.get('assignees') or {})
        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            return _create_version(
                self.context['document'],
                validated_data['file'],
                validated_data['change_description'],
                validated_data['approval_path'],
                self.context['request'].user,
                validated_data['assignees'],
                validated_data['change_type'],
            )


def is_document_author(user, version) -> bool:
    return user.id in (version.document.created_by_id, version.uploaded_by_id)


def can_reassign_step(user, step, version=None) -> bool:
    """
    Zmienić osobę przy oczekującym kroku sekcji z managerem może: wskazana osoba (przekierowanie w sekcji),
    manager sekcji, twórca instrukcji i administrator DOC.
    """
    version = version or step.version
    if not is_managed(step.section) or step.status != ApprovalStep.Status.PENDING:
        return False
    if version.status != DocumentVersion.Status.PENDING:
        return False
    return (
        step.assigned_to_id == user.id
        or step.section in managed_sections(user)
        or is_document_author(user, version)
        or is_doc_admin(user)
    )


def reassign_reason(user, step, version) -> str:
    Reason = StepAssignmentLog.Reason
    # Kolejność: przekierowanie przez wskazaną osobę, zmiana przez twórcę (nawet jeśli jest też managerem), manager
    if step.assigned_to_id == user.id:
        return Reason.FORWARD
    if is_document_author(user, version):
        return Reason.AUTHOR
    if step.section in managed_sections(user):
        return Reason.MANAGER
    return Reason.ADMIN


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
    can_reassign = serializers.SerializerMethodField()
    can_decide = serializers.SerializerMethodField()
    is_my_team = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalStep
        fields = [
            'id', 'order', 'section', 'section_label', 'document', 'version', 'steps',
            'is_managed', 'assigned_to', 'assigned_at', 'can_reassign', 'can_decide', 'is_my_team',
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

    def get_can_reassign(self, obj):
        user = self._user()
        return bool(user and can_reassign_step(user, obj))

    def get_can_decide(self, obj):
        user = self._user()
        return bool(user and can_decide_step(user, obj))

    def get_is_my_team(self, obj):
        """Krok sekcji, której użytkownik jest managerem, a rozpatruje go ktoś inny (lista „Mój zespół”)."""
        user = self._user()
        return bool(user and obj.section in managed_sections(user) and obj.assigned_to_id != user.id)

    def get_section_label(self, obj):
        return DOC_SECTIONS.get(obj.section, obj.section)

    def get_document(self, obj):
        document = obj.version.document
        phase = document.phase
        return {
            'id': document.id,
            'number': document.number,
            'title': document.title,
            'family_id': phase.operation.family_id,
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
        return serialize_steps(obj.version, self._user())


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
