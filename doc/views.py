import hmac
import os
import secrets
import string

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError, Prefetch, Q, OuterRef, Subquery, Count, Max
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.decorators import method_decorator
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from user.services import generate_unique_username
from .models import (
    Family, Operation, Phase, Factory, Department, Document, DocumentVersion, ApprovalStep, ApprovalStepAttachment,
    Notification, ChangeType, StepAssignmentLog,
)
from . import conversion
from .notifications import notify_active_steps, notify_assigned, notify_version_finished, mark_step_read
from .permissions import HasDocAccess, IsDocAdmin, IsDocAdminOrReadOnly, CanAddDocuments
from .roles import (
    is_doc_admin, can_add_documents, set_user_doc_groups, user_sections, member_sections, managed_sections,
    is_managed, section_people, DOC_SECTIONS, SECTION_MANAGERS,
)
from .serializers import (
    FamilySerializer, OperationSerializer, PhaseSerializer, FactorySerializer, DepartmentSerializer,
    DocumentListSerializer, DocumentDetailSerializer, DocumentCreateSerializer, DocumentVersionCreateSerializer,
    OperatorDocumentSerializer, PendingApprovalSerializer, NotificationSerializer, ChangeTypeSerializer,
    can_reassign_step, can_decide_step, reassign_reason, user_display_name,
    DocUserSerializer, DocUserGroupsSerializer, DocUserCreateSerializer,
)

User = get_user_model()

# Notatka do odrzucenia: limity załączników
MAX_NOTE_FILES = 10
MAX_NOTE_FILE_SIZE = 20 * 1024 * 1024


# --- Słowniki --------------------------------------------------------------

class DictionaryViewSet(viewsets.ModelViewSet):
    """Odczyt dla wszystkich z DOC, zmiany dla administratora. ?active=1 zwraca tylko aktywne."""
    permission_classes = [IsDocAdminOrReadOnly]
    parent_field = None

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get('active') in ('1', 'true'):
            queryset = queryset.filter(is_active=True)
        if self.parent_field:
            parent_id = self.request.query_params.get(self.parent_field)
            if parent_id:
                queryset = queryset.filter(**{f"{self.parent_field}_id": parent_id})
        return queryset

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {'detail': 'Nie można usunąć – element jest już używany. Możesz go dezaktywować.'},
                status=status.HTTP_400_BAD_REQUEST,
            )


class FamilyViewSet(DictionaryViewSet):
    queryset = Family.objects.all()
    serializer_class = FamilySerializer


class OperationViewSet(DictionaryViewSet):
    queryset = Operation.objects.all()
    serializer_class = OperationSerializer
    parent_field = 'family'


class PhaseViewSet(DictionaryViewSet):
    queryset = Phase.objects.all()
    serializer_class = PhaseSerializer
    parent_field = 'operation'


class FactoryViewSet(DictionaryViewSet):
    queryset = Factory.objects.all()
    serializer_class = FactorySerializer


class DepartmentViewSet(DictionaryViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer


class ChangeTypeViewSet(DictionaryViewSet):
    """Typy zmian nowej wersji: odczyt dla DOC, zmiany dla administratora. Użytego typu nie da się usunąć."""
    queryset = ChangeType.objects.all()
    serializer_class = ChangeTypeSerializer


# --- Dokumenty (dodający / administrator) ----------------------------------

def _steps_prefetch():
    """Kroki z decyzjami, załącznikami i historią przypisań (do serialize_steps)."""
    return Prefetch(
        'approval_steps',
        queryset=ApprovalStep.objects.select_related('decided_by', 'assigned_to').prefetch_related(
            'attachments',
            Prefetch('assignment_log', queryset=StepAssignmentLog.objects.select_related('from_user', 'to_user', 'by_user')),
        ),
    )


class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                      viewsets.GenericViewSet):
    """
    Lista instrukcji dodającego / administratora.
    ?scope=mine|all – domyślnie dodający widzi swoje, administrator wszystkie; cudze są tylko do podglądu
    (nowe wersje dodaje twórca albo administrator).
    Filtry: ?search=, ?factory=, ?department=, ?status=, ?family=, ?operation=, ?phase=, ?author=,
    ?change_type=, ?date_from=, ?date_to= (ostatnia wersja), ?awaiting_section=, ?assignee= (aktywny krok).
    """
    permission_classes = [CanAddDocuments]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        latest = DocumentVersion.objects.filter(document=OuterRef('pk')).order_by('-version_number')
        queryset = (
            Document.objects
            .select_related('phase__operation__family', 'factory', 'department', 'created_by')
            .prefetch_related(Prefetch(
                'versions',
                queryset=DocumentVersion.objects.select_related('uploaded_by', 'decided_by', 'change_type')
                .prefetch_related(_steps_prefetch()),
            ))
            .annotate(
                latest_status=Subquery(latest.values('status')[:1]),
                latest_change_type=Subquery(latest.values('change_type')[:1]),
                latest_created=Subquery(latest.values('created_at')[:1]),
            )
        )
        user = self.request.user
        if self.action == 'list':
            scope = self.request.query_params.get('scope') or ('all' if is_doc_admin(user) else 'mine')
            if scope != 'all':
                queryset = queryset.filter(created_by=user)

        params = self.request.query_params
        search = params.get('search', '').strip()
        if search:
            search_q = (
                Q(title__icontains=search)
                | Q(phase__name__icontains=search)
                | Q(phase__operation__name__icontains=search)
                | Q(phase__operation__family__name__icontains=search)
            )
            digits = search.upper().removeprefix('INS-').lstrip('0')
            if digits.isdigit():
                search_q |= Q(pk=int(digits))
            queryset = queryset.filter(search_q)
        simple_filters = {
            'factory': 'factory_id', 'department': 'department_id', 'status': 'latest_status',
            'family': 'phase__operation__family_id', 'operation': 'phase__operation_id', 'phase': 'phase_id',
            'author': 'created_by_id', 'change_type': 'latest_change_type',
            'date_from': 'latest_created__date__gte', 'date_to': 'latest_created__date__lte',
        }
        for param, lookup in simple_filters.items():
            if params.get(param):
                queryset = queryset.filter(**{lookup: params[param]})
        return queryset

    def list(self, request, *args, **kwargs):
        documents = list(self.get_queryset())
        # Filtry po aktywnym kroku najnowszej wersji (kolejność kroków liczy się w Pythonie)
        section, assignee = request.query_params.get('awaiting_section'), request.query_params.get('assignee')
        if section or assignee:
            def matches(document):
                versions = list(document.versions.all())
                if not versions or versions[0].status != DocumentVersion.Status.PENDING:
                    return False
                active = versions[0].active_steps()
                return any(
                    (not section or step.section == section)
                    and (not assignee or str(step.assigned_to_id) == assignee)
                    for step in active
                )
            documents = [document for document in documents if matches(document)]
        return Response(DocumentListSerializer(documents, many=True, context={'request': request}).data)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return DocumentDetailSerializer
        if self.action == 'create':
            return DocumentCreateSerializer
        return DocumentListSerializer

    def create(self, request, *args, **kwargs):
        serializer = DocumentCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        detail = self.get_queryset().get(pk=document.pk)
        return Response(DocumentDetailSerializer(detail, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='versions')
    def add_version(self, request, pk=None):
        document = self.get_object()
        if document.created_by_id != request.user.id and not is_doc_admin(request.user):
            return Response({'detail': 'Nową wersję może dodać tylko twórca instrukcji albo administrator.'},
                            status=status.HTTP_403_FORBIDDEN)
        serializer = DocumentVersionCreateSerializer(
            data=request.data, context={'request': request, 'document': document}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        detail = self.get_queryset().get(pk=document.pk)
        return Response(DocumentDetailSerializer(detail, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def authors(self, request):
        """Twórcy instrukcji – do filtra listy."""
        authors = User.objects.filter(doc_documents__isnull=False).distinct().order_by('last_name', 'first_name')
        return Response([{'id': author.id, 'name': user_display_name(author)} for author in authors])


def _steps_queryset():
    return (
        ApprovalStep.objects
        .select_related(
            'decided_by', 'assigned_to', 'version__uploaded_by', 'version__document__factory', 'version__document__department',
            'version__document__phase__operation__family',
        )
        .prefetch_related(Prefetch(
            'version__approval_steps',
            queryset=ApprovalStep.objects.select_related('decided_by', 'assigned_to').prefetch_related(
                'attachments',
                Prefetch('assignment_log', queryset=StepAssignmentLog.objects.select_related('from_user', 'to_user', 'by_user')),
            ),
        ))
    )


class PendingApprovalsView(APIView):
    """
    Aktywne kroki, przy których użytkownik ma coś do zrobienia:
    - manager sekcji: wszystkie aktywne kroki sekcji (przypisanie i ewentualnie decyzja),
    - osoba wskazana przez managera: kroki przypisane do niej,
    - sekcje bez managera: kroki sekcji, do której należy.
    """
    permission_classes = [HasDocAccess]

    def get(self, request):
        user = request.user
        managed = managed_sections(user)
        unmanaged_member = {section for section in member_sections(user) if not is_managed(section)}
        visibility = Q(assigned_to=user) | Q(section__in=managed | unmanaged_member)
        candidates = _steps_queryset().filter(
            visibility,
            status=ApprovalStep.Status.PENDING,
            version__status=DocumentVersion.Status.PENDING,
        ).order_by('version__created_at')
        active = [step for step in candidates if step in step.version.active_steps()]
        return Response(PendingApprovalSerializer(active, many=True, context={'request': request}).data)


class AssignStepView(APIView):
    """
    Zmiana osoby przy oczekującym kroku sekcji z managerem (body: user, comment):
    przekierowanie przez wskazaną osobę, przypisanie przez managera sekcji, zmiana przez twórcę instrukcji
    lub administratora. Nowa osoba musi być z tej samej sekcji. Każda zmiana trafia do logu.
    """
    permission_classes = [HasDocAccess]

    def post(self, request, step_id):
        with transaction.atomic():
            step = get_object_or_404(ApprovalStep.objects.select_related('version'), pk=step_id)
            version = DocumentVersion.objects.select_for_update().select_related('document').get(pk=step.version_id)
            step.refresh_from_db()

            if not can_reassign_step(request.user, step, version):
                return Response({'detail': 'Nie możesz zmienić osoby przy tym kroku.'}, status=status.HTTP_403_FORBIDDEN)

            assignee = section_people(step.section).filter(pk=request.data.get('user')).first()
            if not assignee:
                return Response({'detail': 'Wybierz aktywną osobę z tej sekcji.'}, status=status.HTTP_400_BAD_REQUEST)
            if assignee.pk == step.assigned_to_id:
                return Response({'detail': 'Ta osoba jest już wskazana.'}, status=status.HTTP_400_BAD_REQUEST)

            previous = step.assigned_to
            reason = reassign_reason(request.user, step, version)
            step.assigned_to = assignee
            step.assigned_by = request.user
            step.assigned_at = timezone.now()
            step.save(update_fields=['assigned_to', 'assigned_by', 'assigned_at', 'updated_at'])
            StepAssignmentLog.objects.create(
                step=step, from_user=previous, to_user=assignee, by_user=request.user, reason=reason,
                comment=str(request.data.get('comment', '')).strip()[:2000],
            )
            # Powiadomienie tylko, gdy krok już czeka na decyzję – przyszłe kroki powiadomią po swojej kolejce
            if step in version.active_steps():
                notify_assigned(step, actor=request.user, previous=previous)

        step = _steps_queryset().get(pk=step.pk)
        return Response(PendingApprovalSerializer(step, context={'request': request}).data)


class SectionPeopleView(APIView):
    """
    Osoby, które mogą rozpatrzyć krok sekcji (?section=), do okna wyboru osoby.
    Na górze proponowane: osoby, które już akceptowały tę sekcję w rodzinie (?family= albo ?document=),
    częściej i ostatnio – wyżej; dalej alfabetycznie.
    """
    permission_classes = [HasDocAccess]

    def get(self, request):
        section = request.query_params.get('section', '')
        if section not in DOC_SECTIONS:
            return Response({'detail': 'Nieznana sekcja.'}, status=status.HTTP_400_BAD_REQUEST)

        family_id = request.query_params.get('family')
        document_id = request.query_params.get('document')
        if not family_id and document_id and str(document_id).isdigit():
            family_id = Document.objects.filter(pk=document_id).values_list('phase__operation__family_id', flat=True).first()

        history = {}
        if family_id and str(family_id).isdigit():
            steps = ApprovalStep.objects.filter(section=section, version__document__phase__operation__family_id=family_id)
            # Wskazane osoby; przy krokach bez wskazania (sprzed zmian) – ten, kto zdecydował
            rows = list(steps.filter(assigned_to__isnull=False).values('assigned_to')
                        .annotate(n=Count('id'), last=Max('created_at')).values_list('assigned_to', 'n', 'last'))
            rows += list(steps.filter(assigned_to__isnull=True, decided_by__isnull=False).values('decided_by')
                         .annotate(n=Count('id'), last=Max('created_at')).values_list('decided_by', 'n', 'last'))
            for user_id, count, last in rows:
                prev_count, prev_last = history.get(user_id, (0, None))
                history[user_id] = (prev_count + count, max(filter(None, [prev_last, last])))

        manager_group = SECTION_MANAGERS.get(section)
        people = []
        for person in section_people(section).prefetch_related('groups'):
            count, last = history.get(person.id, (0, None))
            people.append({
                'id': person.id,
                'name': user_display_name(person),
                'is_manager': bool(manager_group) and any(g.name == manager_group for g in person.groups.all()),
                'suggested': count > 0,
                'suggested_count': count,
                '_last': last,
            })
        suggested = sorted((p for p in people if p['suggested']),
                           key=lambda p: (-p['suggested_count'], -(p['_last'].timestamp() if p['_last'] else 0)))
        others = sorted((p for p in people if not p['suggested']), key=lambda p: p['name'].lower())
        for person in people:
            person.pop('_last')
        return Response(suggested + others)


class ApprovalStepDecisionView(APIView):
    """
    Zatwierdzenie / odrzucenie kroku przez osobę z jego sekcji.
    Odrzucenie (powód + opcjonalne pliki "files", np. zrzuty ekranu) kończy całą wersję;
    zatwierdzenie ostatniego kroku zatwierdza wersję (operatorzy ją widzą).
    """
    permission_classes = [HasDocAccess]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, step_id, decision):
        files = request.FILES.getlist('files') if decision == 'reject' else []
        if len(files) > MAX_NOTE_FILES:
            return Response({'detail': f'Możesz dołączyć maksymalnie {MAX_NOTE_FILES} plików.'},
                            status=status.HTTP_400_BAD_REQUEST)
        too_big = next((f for f in files if f.size > MAX_NOTE_FILE_SIZE), None)
        if too_big:
            return Response(
                {'detail': f'Plik {too_big.name} jest za duży (maks. {MAX_NOTE_FILE_SIZE // (1024 * 1024)} MB).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Blokada wersji – dwie osoby z sekcji nie zdecydują jednocześnie
            step = get_object_or_404(ApprovalStep.objects.select_related('version'), pk=step_id)
            version = DocumentVersion.objects.select_for_update().get(pk=step.version_id)
            step.refresh_from_db()

            if not can_decide_step(request.user, step):
                detail = ('Ten krok rozpatruje osoba wskazana przez managera sekcji.'
                          if is_managed(step.section) and step.section in user_sections(request.user)
                          else 'Ten krok akceptuje inna sekcja.')
                return Response({'detail': detail}, status=status.HTTP_403_FORBIDDEN)
            if step not in version.active_steps():
                return Response(
                    {'detail': 'Ten krok nie czeka teraz na decyzję (został już rozpatrzony albo jeszcze nie jego kolej).'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            comment = str(request.data.get('comment', '')).strip()
            if decision == 'reject' and not comment:
                return Response({'detail': 'Podaj powód odrzucenia.'}, status=status.HTTP_400_BAD_REQUEST)

            now = timezone.now()
            step.status = ApprovalStep.Status.APPROVED if decision == 'approve' else ApprovalStep.Status.REJECTED
            step.decided_by = request.user
            step.decided_at = now
            step.comment = comment
            step.save(update_fields=['status', 'decided_by', 'decided_at', 'comment', 'updated_at'])

            for uploaded in files:
                ApprovalStepAttachment.objects.create(
                    step=step,
                    file=uploaded,
                    original_filename=os.path.basename(uploaded.name),
                    content_type=uploaded.content_type or '',
                    uploaded_by=request.user,
                )

            version_finished = decision == 'reject' or not version.active_steps()
            if version_finished:
                version.status = (
                    DocumentVersion.Status.REJECTED if decision == 'reject' else DocumentVersion.Status.APPROVED
                )
                version.decided_by = request.user
                version.decided_at = now
                version.decision_comment = comment
                version.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_comment', 'updated_at'])

            mark_step_read(step)
            if version_finished:
                notify_version_finished(version, actor=request.user, comment=comment)
            else:
                notify_active_steps(version, actor=request.user)

        step = _steps_queryset().get(pk=step.pk)
        return Response(PendingApprovalSerializer(step, context={'request': request}).data)


# --- Powiadomienia -----------------------------------------------------------

NOTIFICATIONS_LIMIT = 30


class NotificationsView(APIView):
    """Ostatnie powiadomienia zalogowanego użytkownika + liczba nieprzeczytanych (front odpytuje cyklicznie)."""
    permission_classes = [HasDocAccess]

    def get(self, request):
        mine = Notification.objects.filter(recipient=request.user)
        unread = mine.filter(read_at__isnull=True).count()
        latest = mine.select_related('actor')[:NOTIFICATIONS_LIMIT]
        return Response({'unread_count': unread, 'results': NotificationSerializer(latest, many=True).data})


class NotificationReadView(APIView):
    """Oznaczenie jednego (notification_id) albo wszystkich powiadomień jako przeczytanych."""
    permission_classes = [HasDocAccess]

    def post(self, request, notification_id=None):
        unread = Notification.objects.filter(recipient=request.user, read_at__isnull=True)
        if notification_id is not None:
            get_object_or_404(Notification, pk=notification_id, recipient=request.user)
            unread = unread.filter(pk=notification_id)
        unread.update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


def _can_see_unapproved(user, version) -> bool:
    """Niezatwierdzoną wersję (i notatki do niej) widzą dodający, administrator i sekcje z jej ścieżki."""
    if can_add_documents(user):
        return True
    path_sections = set(version.approval_steps.values_list('section', flat=True))
    return bool(path_sections & user_sections(user))


@method_decorator(xframe_options_sameorigin, name='dispatch')
class StepAttachmentFileView(APIView):
    """Plik z notatki do decyzji (np. zrzut ekranu przy odrzuceniu)."""
    permission_classes = [HasDocAccess]

    def get(self, request, attachment_id):
        attachment = get_object_or_404(ApprovalStepAttachment.objects.select_related('step__version'), pk=attachment_id)
        if not _can_see_unapproved(request.user, attachment.step.version):
            raise Http404
        try:
            file = attachment.file.open('rb')
        except FileNotFoundError:
            raise Http404
        return FileResponse(file, as_attachment=False, filename=attachment.original_filename)


@method_decorator(xframe_options_sameorigin, name='dispatch')
class DocumentVersionFileView(APIView):
    """
    Plik wersji do wyświetlenia w przeglądarce.
    Zatwierdzone wersje – każdy z dostępem do DOC; pozostałe – dodający, administrator
    oraz osoby z sekcji, które są w ścieżce akceptacji tej wersji.
    """
    permission_classes = [HasDocAccess]

    def get(self, request, version_id):
        version = get_object_or_404(DocumentVersion.objects.select_related('document'), pk=version_id)
        if version.status != DocumentVersion.Status.APPROVED and not _can_see_unapproved(request.user, version):
            raise Http404
        # ?variant=pdf – PDF z konwertera (arkusze); bez parametru – oryginalny plik
        if request.query_params.get('variant') == 'pdf':
            if version.conversion_status != DocumentVersion.ConversionStatus.DONE or not version.pdf_file:
                raise Http404
            source, filename = version.pdf_file, f"{os.path.splitext(version.original_filename)[0]}.pdf"
        else:
            source, filename = version.file, version.original_filename
        try:
            file = source.open('rb')
        except FileNotFoundError:
            raise Http404
        return FileResponse(file, as_attachment=False, filename=filename)


class ConversionCallbackView(APIView):
    """
    Wynik konwersji z BGS-converter (bez sesji użytkownika – uwierzytelnia wspólny klucz X-Api-Key).
    Przyjmowany tylko dla bieżącego zlecenia wersji (job_id), inaczej 409 – konwerter nie ponawia.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, version_id):
        api_key = settings.DOC_CONVERTER_API_KEY
        if not api_key or not hmac.compare_digest(request.headers.get('X-Api-Key', ''), api_key):
            return Response({'detail': 'Nieprawidłowy klucz API.'}, status=status.HTTP_401_UNAUTHORIZED)

        Status = DocumentVersion.ConversionStatus
        with transaction.atomic():
            version = get_object_or_404(DocumentVersion.objects.select_for_update(), pk=version_id)
            if version.conversion_status != Status.PENDING or version.conversion_job_id != request.data.get('job_id'):
                return Response({'detail': 'Nieaktualne zlecenie konwersji.'}, status=status.HTTP_409_CONFLICT)

            pdf = request.FILES.get('pdf')
            if request.data.get('status') == 'done' and pdf:
                version.pdf_file.save(f"{os.path.splitext(version.original_filename)[0]}.pdf", pdf, save=False)
                version.conversion_status = Status.DONE
                version.conversion_error = ''
            else:
                version.conversion_status = Status.FAILED
                version.conversion_error = (request.data.get('error') or 'Konwerter nie zwrócił pliku PDF.')[:2000]
            version.save(update_fields=['pdf_file', 'conversion_status', 'conversion_error', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class RetryConversionView(APIView):
    """Ponowienie konwersji po błędzie (lub gdy zlecenie zbyt długo wisi „w toku”)."""
    permission_classes = [CanAddDocuments]

    def post(self, request, version_id):
        version = get_object_or_404(DocumentVersion.objects.select_related('document'), pk=version_id)
        if not (is_doc_admin(request.user) or version.document.created_by_id == request.user.id):
            raise Http404
        if not conversion.can_retry(version):
            return Response({'detail': 'Tej konwersji nie można teraz ponowić.'}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            conversion.request_conversion(version)
        return Response({'conversion_status': version.conversion_status}, status=status.HTTP_202_ACCEPTED)


# --- Widok operatora -------------------------------------------------------

class OperatorDocumentsView(APIView):
    """
    Panel stanowiska (publiczny, bez logowania): dokumenty fazy z ich najnowszą zatwierdzoną wersją.
    Front odpytuje to cyklicznie (polling). Niezatwierdzone wersje nigdy tu nie trafiają.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        phase_id = request.query_params.get('phase')
        if not phase_id or not str(phase_id).isdigit():
            return Response({'detail': 'Wybierz fazę.'}, status=status.HTTP_400_BAD_REQUEST)

        approved = DocumentVersion.objects.filter(status=DocumentVersion.Status.APPROVED).order_by('-version_number')
        documents = (
            Document.objects
            .filter(phase_id=phase_id, phase__is_active=True, versions__status=DocumentVersion.Status.APPROVED)
            .distinct()
            .order_by('title')
            .prefetch_related(Prefetch('versions', queryset=approved, to_attr='current_versions'))
        )
        return Response(OperatorDocumentSerializer(documents, many=True).data)


class OperatorDictionaryView(APIView):
    """Panel stanowiska: aktywne rodziny / operacje (?family=) / fazy (?operation=) – tylko do odczytu, bez logowania."""
    authentication_classes = []
    permission_classes = [AllowAny]
    model = None
    serializer_class = None
    parent_field = None

    def get(self, request):
        queryset = self.model.objects.filter(is_active=True)
        if self.parent_field:
            parent_id = request.query_params.get(self.parent_field)
            if not parent_id or not str(parent_id).isdigit():
                return Response([])
            queryset = queryset.filter(**{f"{self.parent_field}_id": parent_id, f"{self.parent_field}__is_active": True})
        return Response(self.serializer_class(queryset, many=True).data)


@method_decorator(xframe_options_sameorigin, name='dispatch')
class OperatorVersionFileView(APIView):
    """Panel stanowiska: plik (lub PDF z konwertera, ?variant=pdf) wyłącznie zatwierdzonej wersji – bez logowania."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, version_id):
        version = get_object_or_404(DocumentVersion, pk=version_id, status=DocumentVersion.Status.APPROVED)
        if request.query_params.get('variant') == 'pdf':
            if version.conversion_status != DocumentVersion.ConversionStatus.DONE or not version.pdf_file:
                raise Http404
            source, filename = version.pdf_file, f"{os.path.splitext(version.original_filename)[0]}.pdf"
        else:
            source, filename = version.file, version.original_filename
        try:
            file = source.open('rb')
        except FileNotFoundError:
            raise Http404
        return FileResponse(file, as_attachment=False, filename=filename)


# --- Użytkownicy (panel administratora DOC) --------------------------------

class DocUserViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                     viewsets.GenericViewSet):
    """
    Lista wszystkich użytkowników (żeby można było nadać DOC także osobom z UR),
    zakładanie kont DOC i ustawianie ról / sekcji. ?search=, ?doc_only=1
    """
    permission_classes = [IsDocAdmin]
    serializer_class = DocUserSerializer
    http_method_names = ['get', 'post', 'patch']

    def get_queryset(self):
        queryset = User.objects.prefetch_related('groups').order_by('last_name', 'first_name', 'username')
        params = self.request.query_params
        search = params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search) | Q(first_name__icontains=search)
                | Q(last_name__icontains=search) | Q(email__icontains=search)
            )
        if params.get('doc_only') in ('1', 'true'):
            queryset = queryset.filter(groups__name__startswith='doc_').distinct()
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = DocUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        with transaction.atomic():
            # Konto nieaktywne do czasu ustawienia hasła przez użytkownika ("Nowy użytkownik" na ekranie logowania)
            user = User.objects.create_user(
                username=generate_unique_username(data['first_name'], data['last_name']),
                password=password,
                first_name=data['first_name'],
                last_name=data['last_name'],
                email=data['email'],
                card_code=data['card_code'],
                main_page='DOC',
                is_active=False,
                is_in_change_password=True,
            )
            set_user_doc_groups(
                user, set(data.get('roles', [])) | set(data.get('sections', [])) | set(data.get('managers', []))
            )

        user = self.get_queryset().get(pk=user.pk)
        return Response(DocUserSerializer(user).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        serializer = DocUserGroupsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        current = DocUserSerializer(user).data
        roles = set(data['roles']) if 'roles' in data else set(current['roles'])
        sections = set(data['sections']) if 'sections' in data else set(current['sections'])
        managers = set(data['managers']) if 'managers' in data else set(current['managers'])
        set_user_doc_groups(user, roles | sections | managers)

        user = self.get_queryset().get(pk=user.pk)
        return Response(DocUserSerializer(user).data)
