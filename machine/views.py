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

from .models import Breakdown, AdditionalEndingBreakdownInfo, BreakdownMove, Machine, ClosingBreakdownTypes, ResponsibleForBreakdown, Workshop, Department, WorkShopParticipant
from .serializers import (BreakdownListSerializer, BreakdownCreateSerializer, BreakdownMovePostSerializer, MachineMainSerializer, EndBreakdownSerializer, WorkshopSerializer,
                          MachineFullListSerializer, ClosingBreakdownTypesSerializer, MachineSerializer, BreakdownListSerializerFullHistory, ResponsibleForBreakdownSerializer,
                          FullBreakdownHistorySerializer, DepartamentSerializer, BreakdownMoveToHistorySerializer, BreakdownOptionsResponseSerializer, BreakdownMoveOptionResponseSerializer)
from .services import create_Breakdown_with_initial_move, MoveBreakdownService, EndBreakdownService
from .filters import BreakdownFilter, BreakdownMoveFilter
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


class BreakdownListView(ListAPIView):
    serializer_class = BreakdownListSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return Breakdown.objects.with_last_status().exclude(history__status=BreakdownMove.Status.ENDED).order_by('-created_at')
        

class BreakdownCreateView(CreateAPIView):
    serializer_class = BreakdownCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
            instance = create_Breakdown_with_initial_move(
                user=self.request.user, 
                Breakdown_data=serializer.validated_data
            )


class BreakdownCreateMachineHelper(ListAPIView):
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
    
        recent_machine_ids = (Breakdown.objects.filter(reporter=user)
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
                            .select_related('closing_breakdown_type', 'responsible_for_Breakdown')))
                .order_by('-created_at'))
    

class ClosingBreakdownTypesViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ClosingBreakdownTypesSerializer
    queryset = ClosingBreakdownTypes.objects.all()


class ResponsibleForBreakdownViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ResponsibleForBreakdownSerializer
    queryset = ResponsibleForBreakdown.objects.all()


class WorkshopViewset(viewsets.ModelViewSet):
    serializer_class = WorkshopSerializer
    queryset = Workshop.objects.all()


class ListOfBreakdownsMoves(ListAPIView):
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
        
