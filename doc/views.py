import secrets
import string

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
from rest_framework.views import APIView

from user.services import generate_unique_username
from .models import Family, Operation, Phase, Factory, Department, Document, DocumentVersion
from .permissions import HasDocAccess, IsDocAdmin, IsDocAdminOrReadOnly, CanAddDocuments
from .roles import is_doc_admin, can_add_documents, set_user_doc_groups
from .serializers import (
    FamilySerializer, OperationSerializer, PhaseSerializer, FactorySerializer, DepartmentSerializer,
    DocumentListSerializer, DocumentDetailSerializer, DocumentCreateSerializer, DocumentVersionCreateSerializer,
    DocumentVersionSerializer, OperatorDocumentSerializer,
    DocUserSerializer, DocUserGroupsSerializer, DocUserCreateSerializer,
)

User = get_user_model()


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
            .prefetch_related(Prefetch('versions', queryset=DocumentVersion.objects.select_related('uploaded_by', 'decided_by')))
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


class DocumentVersionDecisionView(APIView):
    """
    TYMCZASOWE: administrator ręcznie zatwierdza / odrzuca wersję,
    dopóki nie powstanie ścieżka akceptacji (Quality / BHP / Production).
    """
    permission_classes = [IsDocAdmin]

    def post(self, request, version_id, decision):
        version = get_object_or_404(DocumentVersion, pk=version_id)
        if version.status != DocumentVersion.Status.PENDING:
            return Response({'detail': 'Ta wersja została już rozpatrzona.'}, status=status.HTTP_400_BAD_REQUEST)

        version.status = (
            DocumentVersion.Status.APPROVED if decision == 'approve' else DocumentVersion.Status.REJECTED
        )
        version.decided_by = request.user
        version.decided_at = timezone.now()
        version.decision_comment = request.data.get('comment', '')
        version.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_comment', 'updated_at'])
        return Response(DocumentVersionSerializer(version).data)


@method_decorator(xframe_options_sameorigin, name='dispatch')
class DocumentVersionFileView(APIView):
    """
    Plik wersji do wyświetlenia w przeglądarce.
    Zatwierdzone wersje – każdy z dostępem do DOC; pozostałe – tylko dodający i administrator.
    """
    permission_classes = [HasDocAccess]

    def get(self, request, version_id):
        version = get_object_or_404(DocumentVersion.objects.select_related('document'), pk=version_id)
        if version.status != DocumentVersion.Status.APPROVED and not can_add_documents(request.user):
            raise Http404
        try:
            file = version.file.open('rb')
        except FileNotFoundError:
            raise Http404
        return FileResponse(file, as_attachment=False, filename=version.original_filename)


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
