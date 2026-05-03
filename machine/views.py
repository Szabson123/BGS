from django.db.models import Prefetch, Case, When, Value, IntegerField
from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import viewsets, status
from rest_framework.filters import SearchFilter
from rest_framework.generics import ListAPIView, CreateAPIView, GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from django_filters import rest_framework as filters

from .models import BreakDown, BreakDownMove, Machine, ClosingBreakdownTypes, ResponsibleForBreakdown
from .serializers import (BreakDownListSerializer, BreakDownCreateSerializer, BreakDownMovePostSerializer, MachineMainSerializer, EndBreakdownSerializer,
                          MachineFullListSerializer, ClosingBreakdownTypesSerializer, MachineSerializer, BreakDownListSerializerFullHistory, ResponsibleForBreakdownSerializer)
from .services import create_breakdown_with_initial_move, MoveBreakDownService, EndBreakdownService
from .filters import BreakDownFilter
from .mixins import WorkshopContextMixin


class CustomPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 60


class MachineViewSet(viewsets.ModelViewSet):
    serializer_class = MachineMainSerializer
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    search_fields = ['name', 'alias']

    def get_queryset(self):
        if self.action == 'machine_full_history':
            return Machine.objects.with_full_history()
        
        return Machine.objects.select_related('department').prefetch_related('breakdowns', 'notes')

    @action(detail=True, methods=['get'], serializer_class=MachineFullListSerializer)
    def machine_full_history(self, request, pk=None):
        machine = self.get_object()
        serializer = self.get_serializer(machine)

        return Response(serializer.data)


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

        if not hasattr(user, 'currentworkshop'):
            return Machine.objects.none()
        
        current_workshop = user.currentworkshop.workshop
    
        recent_machine_ids = (BreakDown.objects.filter(reporter=user)
                            .order_by('-created_at')
                            .values_list('machine_id', flat=True)
                            .distinct()[:3])
            
        return Machine.objects.filter(department__workshop=current_workshop).annotate(
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

        if move_status == 'ED':
            return Response({'error': 'You cant end with this service'}, status=status.HTTP_400_BAD_REQUEST)

        service = MoveBreakDownService(user=self.request.user, status_val=move_status, break_down=break_down, description=description)
        service.execute()

        return Response({"success"}, status=status.HTTP_201_CREATED)
    

class BreakDownMakeEndedMove(GenericAPIView):
    serializer_class = EndBreakdownSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        service = EndBreakdownService(user=self.request.user, **serializer.validated_data)
        service.execute()

        return Response({"success"}, status=status.HTTP_201_CREATED)


class BreakDownListViewToRaport(ListAPIView):
    serializer_class = BreakDownListSerializerFullHistory
    pagination_class = CustomPagination
    filter_backends = [filters.DjangoFilterBackend]
    filterset_class = BreakDownFilter

    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'currentworkshop'):
            return BreakDown.objects.none()
        
        current_workshop = user.currentworkshop.workshop

        return (BreakDown.objects
                .select_related('machine', 'reporter')
                .prefetch_related(
                    Prefetch('history',
                            BreakDownMove.objects
                            .select_related('user')))
                .filter(machine__department__workshop=current_workshop)
                .order_by('-created_at'))
    

class ClosingBreakDownTypesViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ClosingBreakdownTypesSerializer
    queryset = ClosingBreakdownTypes.objects.all()


class ResponsibleForBreakdownViewset(WorkshopContextMixin, viewsets.ModelViewSet):
    serializer_class = ResponsibleForBreakdownSerializer
    queryset = ResponsibleForBreakdown.objects.all()


class ClosingBreakDownTypesHelper(ListAPIView):
    serializer_class = ClosingBreakdownTypesSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'currentworkshop'):
            return ClosingBreakdownTypes.objects.none()
        
        current_workshop = user.currentworkshop.workshop

        return ClosingBreakdownTypes.objects.filter(workshop=current_workshop)
    

class ResponsibleForBreakdownHelper(ListAPIView):
    serializer_class = ResponsibleForBreakdownSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'currentworkshop'):
            return ResponsibleForBreakdown.objects.none()
        
        current_workshop = user.currentworkshop.workshop

        return ResponsibleForBreakdown.objects.filter(workshop=current_workshop)

