from django.db.models import Prefetch, Case, When, Value, IntegerField
from django_filters.rest_framework import DjangoFilterBackend
from django.shortcuts import get_object_or_404

from rest_framework import viewsets, status
from rest_framework.filters import SearchFilter
from rest_framework.generics import ListAPIView, CreateAPIView, GenericAPIView
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django_filters import rest_framework as filters

from .models import BreakDown, AdditionalEndingBreakDownInfo, BreakDownMove, Machine, ClosingBreakdownTypes, ResponsibleForBreakdown, Workshop, Department, WorkShopParticipant
from .serializers import (BreakDownListSerializer, BreakDownCreateSerializer, BreakDownMovePostSerializer, MachineMainSerializer, EndBreakdownSerializer, WorkshopSerializer,
                          MachineFullListSerializer, ClosingBreakdownTypesSerializer, MachineSerializer, BreakDownListSerializerFullHistory, ResponsibleForBreakdownSerializer,
                          FullBreakDownHistorySerializer, DepartamentSerializer, BreakDownMoveToHistorySerializer, BreakdownOptionsResponseSerializer, BreakdownMoveOptionResponseSerializer)
from .services import create_breakdown_with_initial_move, MoveBreakDownService, EndBreakdownService
from .filters import BreakDownFilter, BreakDownMoveFilter
from .mixins import WorkshopContextMixin, CurrentWorkshopMixin

from user.models import CustomUser


class CustomPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 60


class DepartmentViewSet(viewsets.ModelViewSet):
    serializer_class = DepartamentSerializer
    queryset = Department.objects.all()


class MachinesInCurrentWorkshop(ListAPIView):
    serializer_class = MachineMainSerializer
    permission_classes = [IsAuthenticated]
    queryset = Machine.objects.all()
    
    def get_queryset(self):
        qs = super().get_queryset()
        return Machine.objects.select_related('workshop', 'department')
    

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
    serializer_class =  FullBreakDownHistorySerializer
    pagination_class = CustomPagination
    
    def get_queryset(self):
        machine_id = self.kwargs.get('machine_id')
        queryset = BreakDown.objects.select_related('reporter', 'machine').prefetch_related(
            Prefetch(
                'history',
                queryset=BreakDownMove.objects.select_related('user')
            )
        ).filter(machine=machine_id).order_by('-created_at')

        return queryset


class BreakDownListView(ListAPIView):
    serializer_class = BreakDownListSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return BreakDown.objects.with_last_status().exclude(history__status=BreakDownMove.Status.ENDED).order_by('-created_at')
        

class BreakDownCreateView(CreateAPIView):
    serializer_class = BreakDownCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
            instance = create_breakdown_with_initial_move(
                user=self.request.user, 
                breakdown_data=serializer.validated_data
            )


class BreakDownCreateMachineHelper(ListAPIView):
    serializer_class = MachineSerializer
    queryset = Machine.objects.none()
    permission_classes = [IsAuthenticated]
    filter_backends = [SearchFilter]
    search_fields = ['name', 'alias']

    def get_queryset(self):
        user = self.request.user

        if not hasattr(user, 'currentdepartment'):
            return Machine.objects.none()
        
        current_department = user.currentdepartment.department
    
        recent_machine_ids = (BreakDown.objects.filter(reporter=user)
                            .order_by('-created_at')
                            .values_list('machine_id', flat=True)
                            .distinct()[:3])
            
        return Machine.objects.filter(department=current_department).annotate(
            priority_group=Case(
                When(id__in=recent_machine_ids, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-priority_group', 'name')
        

class BreakDownMakeMove(GenericAPIView):
    serializer_class = BreakDownMovePostSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        move_status = serializer.validated_data['status'] 
        break_down = serializer.validated_data['break_down']
        description = serializer.validated_data['description']

        if move_status == BreakDownMove.Status.ENDED:
            return Response({'error': 'You cant end with this service'}, status=status.HTTP_400_BAD_REQUEST)

        service = MoveBreakDownService(user=self.request.user, status_val=move_status, break_down=break_down, description=description)
        service.execute()

        return Response({"detail": "success"}, status=status.HTTP_201_CREATED)
    

class BreakDownMakeEndedMove(GenericAPIView):
    serializer_class = EndBreakdownSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = EndBreakdownService(user=self.request.user, **serializer.validated_data)
        service.execute()

        return Response({"detail": "success"}, status=status.HTTP_201_CREATED)


class BreakDownListViewToReport(CurrentWorkshopMixin, ListAPIView):
    queryset = BreakDown.objects.all()
    serializer_class = BreakDownListSerializerFullHistory
    pagination_class = CustomPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakDownFilter
    workshop_lookup_field = 'machine__workshop'

    def get_queryset(self):
        qs = super().get_queryset()

        return (qs
                .select_related('machine', 'reporter')
                .prefetch_related(
                    Prefetch('history',
                            BreakDownMove.objects
                            .select_related('user')),
                    Prefetch('additional',
                             AdditionalEndingBreakDownInfo.objects
                            .select_related('closing_break_down_type', 'responsible_for_breakdown')))
                .order_by('-created_at'))
    

class ClosingBreakDownTypesViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ClosingBreakdownTypesSerializer
    queryset = ClosingBreakdownTypes.objects.all()


class ResponsibleForBreakdownViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ResponsibleForBreakdownSerializer
    queryset = ResponsibleForBreakdown.objects.all()


class WorkshopViewset(viewsets.ModelViewSet):
    serializer_class = WorkshopSerializer
    queryset = Workshop.objects.all() # narazie wszystkie ptoem tylko admin/owner


class ListOfBreakDownsMoves(ListAPIView):
    serializer_class = BreakDownMoveToHistorySerializer
    pagination_class = CustomPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakDownMoveFilter
    workshop_lookup_field = 'break_down__machine__workshop'
    queryset = BreakDownMove.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        return (qs
                .select_related('user', 'break_down__reporter', 'break_down__machine', 'break_down__machine__department', 'break_down__machine__workshop')
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

        priorities = [{"value": k, "label": v} for k, v in BreakDown.Priority.choices]
        statuses = [{"value": k, "label": v} for k, v in BreakDownMove.Status.choices]

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

        statuses = [{"value": k, "label": v} for k, v in BreakDownMove.Status.choices]

        data = {
            "departments": departments,
            "machines": machines,
            "users": users,
            "statuses": statuses
        }

        serializer = BreakdownMoveOptionResponseSerializer(data)

        return Response(serializer.data)
        
