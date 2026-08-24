from django.db.models import Prefetch, Case, When, Value, IntegerField, Q
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404
from django.db import transaction

from rest_framework import viewsets, status
from rest_framework.filters import SearchFilter
from rest_framework.generics import ListAPIView, CreateAPIView, GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django_filters import rest_framework as filters
from rest_framework.parsers import MultiPartParser, FormParser

from .models import (Breakdown, AdditionalEndingBreakdownInfo, BreakdownMove, Machine, ClosingBreakdownTypes, ResponsibleForBreakdown, Workshop, Department, WorkshopParticipant,
                     MachineNotes, CurrentWorkshop, CurrentDepartment, WorkSchedulePreset, ScheduleBreak)
from .serializers import (BreakdownListSerializer, BreakdownCreateSerializer, BreakdownMovePostSerializer, MachineMainSerializer, EndBreakdownSerializer, WorkshopSerializer,
                          MachineFullListSerializer, ClosingBreakdownTypesSerializer, MachineSerializer, BreakdownListSerializerFullHistory, ResponsibleForBreakdownSerializer,
                          FullBreakdownHistorySerializer, DepartmentSerializer, BreakdownMoveToHistorySerializer, BreakdownOptionsResponseSerializer, BreakdownMoveOptionResponseSerializer,
                          EndBreakdownOptionsSerializer, WorkshopParticipantSerializer, UserSerializer, MachineNotesSerializer, URProfilePanelSerializer, DepartmentToggleSerializer,
                          WorkSchedulePresetSerializer, ScheduleBreakSerializer, MachineSetScheduleSerializer)
from .services import create_breakdown_with_initial_move, MoveBreakdownService, EndBreakdownService, get_machine_break_status
from .filters import BreakdownFilter, BreakdownMoveFilter
from .mixins import WorkshopContextMixin, CurrentWorkshopMixin, CurrentDepartmentsMixin
from .permissions import IsURAdminOrOwnerOrReadOnlyParticipant

from user.models import CustomUser


class CustomPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 60


class DepartmentViewSet(viewsets.ModelViewSet):
    serializer_class = DepartmentSerializer
    queryset = Department.objects.all()


class MachinesInCurrentWorkshop(CurrentWorkshopMixin, ListAPIView):
    serializer_class = MachineMainSerializer
    permission_classes = [IsAuthenticated]
    queryset = Machine.objects.all()
    workshop_lookup_field = 'workshop'
    
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related('workshop', 'department')
    

class MachineViewSet(viewsets.ModelViewSet):
    serializer_class = MachineMainSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        workshop_id = self.kwargs.get('workshop_id')
        return Machine.objects.select_related('workshop', 'department').filter(workshop=workshop_id)
    
    def perform_create(self, serializer):
        workshop_id = self.kwargs.get('workshop_id')
        workshop = get_object_or_404(Workshop, pk=workshop_id)
        serializer.save(workshop=workshop)


class BreakdownListToMachine(ListAPIView):
    serializer_class =  FullBreakdownHistorySerializer
    pagination_class = CustomPagination
    
    def get_queryset(self):
        machine_id = self.kwargs.get('machine_id')
        queryset = Breakdown.objects.select_related('reporter', 'machine').prefetch_related(
            Prefetch(
                'history',
                queryset=BreakdownMove.objects.select_related('user')
            )
        ).filter(machine=machine_id).order_by('-created_at')

        return queryset


class BreakdownListView(CurrentWorkshopMixin, ListAPIView):
    serializer_class = BreakdownListSerializer
    permission_classes = [IsAuthenticated]
    workshop_lookup_field = 'machine__workshop'
    queryset = Breakdown.objects.all()
    
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.with_last_status().exclude(history__status=BreakdownMove.Status.ENDED).order_by('-created_at')
        

class BreakdownCreateView(CreateAPIView):
    serializer_class = BreakdownCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
            instance = create_breakdown_with_initial_move(
                user=self.request.user, 
                Breakdown_data=serializer.validated_data
            )


class BreakdownCreateMachineHelper(ListAPIView):
    serializer_class = MachineSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [SearchFilter]
    search_fields = ['name', 'alias']

    def get_queryset(self):
        user = self.request.user

        current_department_ids = list(
            user.currentdepartments.values_list('department_id', flat=True)
        )

        if not current_department_ids:
            return Machine.objects.none()

        recent_machine_ids = list(
            Breakdown.objects.filter(reporter=user)
            .order_by('-created_at')
            .values_list('machine_id', flat=True)
            .distinct()[:3]
        )

        return Machine.objects.filter(
            department_id__in=current_department_ids
        ).annotate(
            priority_group=Case(
                When(id__in=recent_machine_ids, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-priority_group', 'name')
        

class BreakdownMakeMove(GenericAPIView):
    serializer_class = BreakdownMovePostSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        move_status = serializer.validated_data['status'] 
        breakdown = serializer.validated_data['breakdown']
        description = serializer.validated_data['description']

        if move_status == BreakdownMove.Status.ENDED:
            return Response({'error': 'You cant end with this service'}, status=status.HTTP_400_BAD_REQUEST)

        service = MoveBreakdownService(user=self.request.user, status_val=move_status, breakdown=breakdown, description=description)
        service.execute()

        return Response({"detail": "success"}, status=status.HTTP_201_CREATED)
    

class BreakdownMakeEndedMove(GenericAPIView):
    serializer_class = EndBreakdownSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = EndBreakdownService(user=self.request.user, **serializer.validated_data)
        service.execute()

        return Response({"detail": "success"}, status=status.HTTP_201_CREATED)


class BreakdownListViewToReport(CurrentWorkshopMixin, ListAPIView):
    queryset = Breakdown.objects.all()
    serializer_class = BreakdownListSerializerFullHistory
    pagination_class = CustomPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakdownFilter
    workshop_lookup_field = 'machine__workshop'

    def get_queryset(self):
        qs = super().get_queryset()

        return (qs
                .select_related('machine', 'reporter')
                .prefetch_related(
                    Prefetch('history',
                            BreakdownMove.objects
                            .select_related('user')),
                    Prefetch('additional',
                             AdditionalEndingBreakdownInfo.objects
                            .select_related('closing_breakdown_type', 'responsible_for_breakdown')))
                .order_by('-created_at'))
    

class ClosingBreakdownTypesViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ClosingBreakdownTypesSerializer
    queryset = ClosingBreakdownTypes.objects.all()


class ResponsibleForBreakdownViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ResponsibleForBreakdownSerializer
    queryset = ResponsibleForBreakdown.objects.all()


class WorkshopViewset(viewsets.ModelViewSet):
    serializer_class = WorkshopSerializer
    permission_classes = [IsURAdminOrOwnerOrReadOnlyParticipant]

    def get_queryset(self):
        user = self.request.user

        if not user or not user.is_authenticated:
            return Workshop.objects.none()

        if user.is_superuser or user.groups.filter(name__in=['ur_admin', 'ur_owner']).exists():
            return Workshop.objects.filter(
                    Q(workshopparticipant__user=user) | Q(owner=user)
                ).distinct()

    def perform_create(self, serializer):
        user = self.request.user

        with transaction.atomic():
            # 1. Zapisujemy warsztat ustawiając zalogowanego użytkownika jako ownera
            workshop = serializer.save(owner=user)

            # 2. Automatycznie dodajemy twórcę jako uczestnika (WorkshopParticipant)
            WorkshopParticipant.objects.get_or_create(
                user=user,
                workshop=workshop
            )

        


class ListOfBreakdownsMoves(CurrentWorkshopMixin, ListAPIView):
    serializer_class = BreakdownMoveToHistorySerializer
    pagination_class = CustomPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakdownMoveFilter
    workshop_lookup_field = 'breakdown__machine__workshop'
    queryset = BreakdownMove.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        return (qs
                .select_related('user', 'breakdown__reporter', 'breakdown__machine', 'breakdown__machine__department', 'breakdown__machine__workshop')
                .order_by('-created_at'))
    
class BreakdownOptionsView(CurrentWorkshopMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = BreakdownOptionsResponseSerializer

    def get(self, request):
        workshop = self.get_workshop()

        if not workshop:
            return Response({"error": "Workshop not found"}, status=status.HTTP_404_NOT_FOUND)

        machines = Machine.objects.filter(workshop=workshop).values('id', 'name')
        responsibles = ResponsibleForBreakdown.objects.filter(workshop=workshop).values('id', 'name')
        close_types = ClosingBreakdownTypes.objects.filter(workshop=workshop).values('id', 'name')

        priorities = [{"value": k, "label": v} for k, v in Breakdown.Priority.choices]
        statuses = [{"value": k, "label": v} for k, v in BreakdownMove.Status.choices]

        data = {
            "machines": machines,
            "priorities": priorities,
            "statuses": statuses,
            "responsibles": responsibles,
            "close_types": close_types
        }

        serializer = BreakdownOptionsResponseSerializer(data)
        return Response(serializer.data)
    

class BreakdownMoveOptionView(CurrentWorkshopMixin, GenericAPIView):
    serializer_class = BreakdownMoveOptionResponseSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workshop = self.get_workshop()

        if not workshop:
            return Response({"error": "Workshop not found"}, status=status.HTTP_404_NOT_FOUND)
        
        departments = Department.objects.all().values('id', 'name')
        machines = Machine.objects.filter(workshop=workshop).values('id', 'name')

        users = CustomUser.objects.filter(workshopparticipant__workshop=workshop)

        statuses = [{"value": k, "label": v} for k, v in BreakdownMove.Status.choices]

        data = {
            "departments": departments,
            "machines": machines,
            "users": users,
            "statuses": statuses
        }

        serializer = BreakdownMoveOptionResponseSerializer(data)
        return Response(serializer.data)
        

class EndBreakdownOptionsView(CurrentWorkshopMixin, GenericAPIView):
    serializer_class = EndBreakdownOptionsSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        workshop = self.get_workshop()

        responsibles = ResponsibleForBreakdown.objects.filter(workshop=workshop).values('id', 'name')
        close_types = ClosingBreakdownTypes.objects.filter(workshop=workshop).values('id', 'name')

        data = {
            "responsibles": responsibles,
            "close_types": close_types
        }

        serializer = EndBreakdownOptionsSerializer(data)
        return Response(serializer.data)


class WorkshopParticipantViewset(viewsets.ModelViewSet):
    serializer_class = WorkshopParticipantSerializer
    queryset = WorkshopParticipant.objects.all()

    def get_queryset(self):
        workshop_id = self.kwargs.get('workshop_id')
        qs = WorkshopParticipant.objects.select_related('user', 'workshop').filter(workshop=workshop_id)
        return qs
    
    def perform_create(self, serializer):
        workshop_id = self.kwargs.get('workshop_id')
        workshop = get_object_or_404(Workshop, pk=workshop_id)
        serializer.save(workshop=workshop)
    
    @action(detail=False, methods=['get'], serializer_class=UserSerializer)
    def get_available_users_to_add(self, request, *args, **kwargs):
        workshop_id = self.kwargs.get('workshop_id')
        
        qs = CustomUser.objects.exclude(workshopparticipant__workshop_id=workshop_id)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    

class MachineNotesViewSet(viewsets.ModelViewSet):
    serializer_class = MachineNotesSerializer
    permission_classes = [IsAuthenticated]
    
    parser_classes = [MultiPartParser, FormParser]

    def perform_create(self, serializer):
        machine_id = self.kwargs.get('machine_id')
        machine = get_object_or_404(Machine, pk=machine_id)
        serializer.save(created_by=self.request.user, machine=machine)

    def get_queryset(self):
        return MachineNotes.objects.select_related('created_by').filter(machine_id=self.kwargs.get('machine_id')).order_by('-created_at')


class URProfilePanel(GenericAPIView):
    serializer_class = URProfilePanelSerializer
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = self.request.user

        current_workshop = get_object_or_404(CurrentWorkshop, user=user)
        avaible_workshops = Workshop.objects.filter(workshopparticipant__user=user)

        data = {
            'current_workshop': current_workshop,
            'avaible_workshops': avaible_workshops,
            'user': user
        }

        serializer = URProfilePanelSerializer(data)

        return Response(serializer.data)


class ChangingCurrentWorkshop(GenericAPIView):
    
    def post(self, request, *args, **kwargs):
        user = self.request.user
        workshop_id = request.data.get('workshop_id')

        if not workshop_id:
            return Response(
                {"error": "Wymagane pole 'workshop_id'."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            workshop_obj = Workshop.objects.get(id=workshop_id)
        except Workshop.DoesNotExist:
            return Response(
                {"error": "Podany warsztat nie istnieje."}, 
                status=status.HTTP_404_NOT_FOUND
            )

        current_workshop, created = CurrentWorkshop.objects.update_or_create(
            user=user,
            defaults={'workshop': workshop_obj}
        )

        message = "Utworzono nowy aktualny warsztat." if created else "Zaktualizowano aktualny warsztat."
        
        return Response(
            {"message": message, "workshop_id": current_workshop.workshop.id}, 
            status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED
        )


class ToggleCurrentDepartmentView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        department_id = request.data.get('department_id')
        user = request.user

        if not department_id:
            return Response({"detail": "department_id jest wymagane."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            department = Department.objects.get(id=department_id)
        except Department.DoesNotExist:
            return Response({"detail": "Departament nie istnieje."}, status=status.HTTP_404_NOT_FOUND)

        current_dept = CurrentDepartment.objects.filter(user=user, department=department).first()

        if current_dept:
            current_dept.delete()
            return Response({
                "detail": f"Odłączono z departamentu: {department.name}",
                "department_id": department.id,
                "is_active": False
            }, status=status.HTTP_200_OK)
        
        else:
            CurrentDepartment.objects.create(user=user, department=department)
            return Response({
                "detail": f"Dołączono do departamentu: {department.name}",
                "department_id": department.id,
                "is_active": True
            }, status=status.HTTP_200_OK)


class DepartmentListView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        departments = Department.objects.all()

        selected_ids = set(
            CurrentDepartment.objects.filter(user=request.user)
            .values_list('department_id', flat=True)
        )

        serializer = DepartmentToggleSerializer(
            departments, 
            many=True, 
            context={'request': request, 'selected_department_ids': selected_ids}
        )
        return Response(serializer.data)


class BreakdownListViewForDepartments(CurrentDepartmentsMixin, ListAPIView):
    queryset = Breakdown.objects.all()
    serializer_class = BreakdownListSerializerFullHistory
    pagination_class = CustomPagination
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakdownFilter
    department_lookup_field = 'machine__department_id__in'

    def get_queryset(self):
        qs = super().get_queryset()

        return (qs
                .select_related('machine', 'reporter')
                .prefetch_related(
                    Prefetch(
                        'history',
                        queryset=BreakdownMove.objects.select_related('user')
                    ),
                    Prefetch(
                        'additional',
                        queryset=AdditionalEndingBreakdownInfo.objects.select_related(
                            'closing_breakdown_type', 
                            'responsible_for_breakdown'
                        )
                    )
                )
                .order_by('-created_at'))


class MachinesInCurrentDepartments(CurrentDepartmentsMixin, ListAPIView):
    serializer_class = MachineMainSerializer
    permission_classes = [IsAuthenticated]
    queryset = Machine.objects.all()
    department_lookup_field = 'department_id__in'
    
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.select_related('workshop', 'department').prefetch_related('schedules__breaks')


class WorkSchedulePresetViewSet(CurrentDepartmentsMixin, viewsets.ModelViewSet):
    serializer_class = WorkSchedulePresetSerializer
    queryset = WorkSchedulePreset.objects.prefetch_related('breaks').select_related('machine', 'machine__department').all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.DjangoFilterBackend]
    filterset_fields = ['machine', 'is_active']
    department_lookup_field = 'machine__department_id__in'

    def get_queryset(self):
        dept_ids = self.get_user_department_ids()
        qs = super(CurrentDepartmentsMixin, self).get_queryset()
        if not dept_ids:
            return qs.none()
        return qs.filter(Q(machine__department_id__in=dept_ids) | Q(machine__isnull=True))

    def perform_create(self, serializer):
        machine_id = self.request.data.get('machine')
        if machine_id:
            user = self.request.user
            dept_ids = list(user.currentdepartments.values_list('department_id', flat=True))
            if dept_ids:
                machine = get_object_or_404(Machine, pk=machine_id, department_id__in=dept_ids)
            else:
                machine = get_object_or_404(Machine, pk=machine_id)
            serializer.save(machine=machine)
        else:
            serializer.save()

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        preset = self.get_object()
        with transaction.atomic():
            WorkSchedulePreset.objects.filter(machine=preset.machine).update(is_active=False)
            preset.is_active = True
            preset.save()
        return Response(self.get_serializer(preset).data, status=status.HTTP_200_OK)


class ScheduleBreakViewSet(CurrentDepartmentsMixin, viewsets.ModelViewSet):
    serializer_class = ScheduleBreakSerializer
    queryset = ScheduleBreak.objects.select_related('preset__machine').all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.DjangoFilterBackend]
    filterset_fields = ['preset']
    department_lookup_field = 'preset__machine__department_id__in'

    def get_queryset(self):
        dept_ids = self.get_user_department_ids()
        qs = super(CurrentDepartmentsMixin, self).get_queryset()
        if not dept_ids:
            return qs.none()
        return qs.filter(Q(preset__machine__department_id__in=dept_ids) | Q(preset__machine__isnull=True))


class MachineBreakStatusView(GenericAPIView):
    permission_classes = []

    def get(self, request, machine_id):
        machine = get_object_or_404(
            Machine.objects.prefetch_related('schedules__breaks'),
            pk=machine_id
        )
        status_data = get_machine_break_status(machine)
        return Response(status_data, status=status.HTTP_200_OK)


class MachineSetScheduleView(GenericAPIView):
    serializer_class = MachineSetScheduleSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, machine_id):
        machine = get_object_or_404(Machine, pk=machine_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        preset_id = serializer.validated_data.get('schedule_preset_id')
        with transaction.atomic():
            WorkSchedulePreset.objects.filter(machine=machine).update(is_active=False)
            active_name = None
            if preset_id is not None:
                preset = get_object_or_404(WorkSchedulePreset, pk=preset_id, machine=machine)
                preset.is_active = True
                preset.save()
                active_name = preset.name

        return Response({
            "message": "Tryb pracy maszyny został zaktualizowany.",
            "machine_id": machine.id,
            "machine_name": machine.name,
            "active_schedule_id": preset_id,
            "active_schedule_name": active_name
        }, status=status.HTTP_200_OK)
