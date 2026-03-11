from django.shortcuts import render
from django.db.models import Prefetch, Window, F, Q
from django.db.models.functions import RowNumber
from django.db import transaction

from rest_framework import viewsets, status
from rest_framework.generics import ListAPIView, CreateAPIView, GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination

from .models import BreakDown, BreakDownMove, Machine
from .serializers import (BreakDownListSerializer, BreakDownCreateSerializer, BreakDownMovePostSerializer, MachineMainSerializer,
                          MachineFullListSerializer)

from .services import create_breakdown_with_initial_move, move_breakdown


class CustomPagination(PageNumberPagination):
    page_size = 20
    max_page_size = 60


class MachineViewSet(viewsets.ModelViewSet):
    serializer_class = MachineMainSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        if self.action == 'machine_full_history':
            return Machine.objects.with_full_history()
        
        return Machine.objects.select_related('workshop').prefetch_related('breakdowns', 'notes')

    @action(detail=True, methods=['get'], serializer_class=MachineFullListSerializer)
    def machine_full_history(self, request, pk=None):
        machine = self.get_object()
        serializer = self.get_serializer(machine)

        return Response(serializer.data)


class BreakDownListView(ListAPIView):
    serializer_class = BreakDownListSerializer
    
    def get_queryset(self):
        statuses = BreakDownMove.objects \
        .select_related('user') \
        .annotate(
            row_number=Window( # Deklaracja Wiaderka
                expression=RowNumber(), # Deklaracja że będziemy numerować rzędy w "Wiaderku"
                partition_by=[F('break_down_id')], # Liczby będą niezależne od obiektu BreakDown czyli bedziemy restować nasze liczby co breakdown
                order_by=F('time').desc() # Jak numerujemy
            )
        ).filter(row_number=1) # Wycinamy wszystko oprócz naszej jedynki 2. W moim Django 6.0 działa natywnie w Postgresql

        break_downs = (BreakDown.objects 
                   .select_related('reporter', 'machine') 
                   .exclude(history__status=BreakDownMove.Status.ENDED) # Zapamiętać że to filtr na zakończone czyli nie mamy reaktywacji awari zakonczona to zakonczona
                   .prefetch_related(
                       Prefetch('history', queryset=statuses, to_attr='latest_status')
                   ))
                   
        
        return break_downs
        

class BreakDownCreateView(CreateAPIView):
    serializer_class = BreakDownCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
            instance = create_breakdown_with_initial_move(
                user=self.request.user, 
                breakdown_data=serializer.validated_data
            )


class BreakDownMakeMove(GenericAPIView):
    serializer_class = BreakDownMovePostSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        move_status = serializer.validated_data['status'] 
        break_down = serializer.validated_data['break_down']
        description = serializer.validated_data['description']

        obj = move_breakdown(
            user=request.user,
            status_val=move_status,
            break_down=break_down,
            description=description
        )

        return Response({"success": f"{obj.pk}"}, status=status.HTTP_201_CREATED)
    

