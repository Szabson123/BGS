import hmac
import os
import secrets
import string

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError, Prefetch, Q, OuterRef, Subquery
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
)
from . import conversion
from .permissions import HasDocAccess, IsDocAdmin, IsDocAdminOrReadOnly, CanAddDocuments
from .roles import is_doc_admin, can_add_documents, set_user_doc_groups, user_sections
from .serializers import (
    FamilySerializer, OperationSerializer, PhaseSerializer, FactorySerializer, DepartmentSerializer,
    DocumentListSerializer, DocumentDetailSerializer, DocumentCreateSerializer, DocumentVersionCreateSerializer,
    OperatorDocumentSerializer, PendingApprovalSerializer,
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


# --- Dokumenty (dodający / administrator) ----------------------------------

class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                      viewsets.GenericViewSet):
    """
    Dodający widzi swoje dokumenty, administrator wszystkie.
    Filtry: ?search=, ?factory=, ?department=, ?status= (status najnowszej wersji).
    """
    permission_classes = [CanAddDocuments]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        latest_status = DocumentVersion.objects.filter(document=OuterRef('pk')).order_by('-version_number')
        queryset = (
            Document.objects
            .select_related('phase__operation__family', 'factory', 'department', 'created_by')
            .prefetch_related(Prefetch(
                'versions',
                queryset=DocumentVersion.objects.select_related('uploaded_by', 'decided_by').prefetch_related(
                    Prefetch('approval_steps', queryset=ApprovalStep.objects.select_related('decided_by').prefetch_related('attachments'))
                ),
            ))
            .annotate(latest_status=Subquery(latest_status.values('status')[:1]))
        )
        if not is_doc_admin(self.request.user):
            queryset = queryset.filter(created_by=self.request.user)

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
        if params.get('factory'):
            queryset = queryset.filter(factory_id=params['factory'])
        if params.get('department'):
            queryset = queryset.filter(department_id=params['department'])
        if params.get('status'):
            queryset = queryset.filter(latest_status=params['status'])
        return queryset

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
        return Response(DocumentDetailSerializer(detail).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='versions')
    def add_version(self, request, pk=None):
        document = self.get_object()
        serializer = DocumentVersionCreateSerializer(
            data=request.data, context={'request': request, 'document': document}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        detail = self.get_queryset().get(pk=document.pk)
        return Response(DocumentDetailSerializer(detail).data, status=status.HTTP_201_CREATED)


def _steps_queryset():
    return (
        ApprovalStep.objects
        .select_related(
            'decided_by', 'version__uploaded_by', 'version__document__factory', 'version__document__department',
            'version__document__phase__operation__family',
        )
        .prefetch_related(Prefetch('version__approval_steps', queryset=ApprovalStep.objects.select_related('decided_by').prefetch_related('attachments')))
    )


class PendingApprovalsView(APIView):
    """Kroki, które teraz czekają na decyzję sekcji zalogowanego użytkownika."""
    permission_classes = [HasDocAccess]

    def get(self, request):
        sections = user_sections(request.user)
        if not sections:
            return Response([])
        candidates = _steps_queryset().filter(
            section__in=sections,
            status=ApprovalStep.Status.PENDING,
            version__status=DocumentVersion.Status.PENDING,
        ).order_by('version__created_at')
        active = [step for step in candidates if step in step.version.active_steps()]
        return Response(PendingApprovalSerializer(active, many=True).data)


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

            if step.section not in user_sections(request.user):
                return Response({'detail': 'Ten krok akceptuje inna sekcja.'}, status=status.HTTP_403_FORBIDDEN)
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

        step = _steps_queryset().get(pk=step.pk)
        return Response(PendingApprovalSerializer(step).data)


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
    """Dokumenty fazy z ich najnowszą zatwierdzoną wersją. Front odpytuje to cyklicznie (polling)."""
    permission_classes = [HasDocAccess]

    def get(self, request):
        phase_id = request.query_params.get('phase')
        if not phase_id:
            return Response({'detail': 'Wybierz fazę.'}, status=status.HTTP_400_BAD_REQUEST)

        approved = DocumentVersion.objects.filter(status=DocumentVersion.Status.APPROVED).order_by('-version_number')
        documents = (
            Document.objects
            .filter(phase_id=phase_id, versions__status=DocumentVersion.Status.APPROVED)
            .distinct()
            .order_by('title')
            .prefetch_related(Prefetch('versions', queryset=approved, to_attr='current_versions'))
        )
        return Response(OperatorDocumentSerializer(documents, many=True).data)


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
            set_user_doc_groups(user, set(data.get('roles', [])) | set(data.get('sections', [])))

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
        set_user_doc_groups(user, roles | sections)

        user = self.get_queryset().get(pk=user.pk)
        return Response(DocUserSerializer(user).data)
