from django.shortcuts import render
from django.db.models import Prefetch
from django.db import transaction

from rest_framework import viewsets, status
from rest_framework.generics import ListAPIView, CreateAPIView, GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action

from .models import BreakDown, BreakDownMove, Machine
from .serializers import (BreakDownListSerializer, BreakDownCreateSerializer, BreakDownMove, BreakDownMovePostSerializer, MachineMainSerializer,
                          MachineFullListSerializer)


class MachineViewSet(viewsets.ModelViewSet):
    serializer_class = MachineMainSerializer

    def get_queryset(self):
        if self.action == 'machine_full_history':
            return Machine.objects.select_related('workshop').prefetch_related(
                'notes',
                Prefetch(
                    'breakdowns',
                    BreakDown.objects.select_related('reporter').prefetch_related(
                        Prefetch(
                            'history',
                            BreakDownMove.objects.select_related('user')
                        )
                    )
                )
            )
        return Machine.objects.select_related('workshop').prefetch_related('breakdowns', 'notes')

    @action(detail=True, methods=['get'], serializer_class=MachineFullListSerializer)
    def machine_full_history(self, request, pk=None):
        machine = self.get_object()
        serializer = self.get_serializer(machine)

        return Response(serializer.data)


class BreakDownListView(ListAPIView):
    serializer_class = BreakDownListSerializer
    
    def get_queryset(self):
        break_downs = (BreakDown.objects
                   .select_related('reporter', 'machine')
                   .prefetch_related('history')
        )
        
        return break_downs
        

class BreakDownCreateView(CreateAPIView):
    serializer_class = BreakDownCreateSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):

        with transaction.atomic():
            instance = serializer.save(reporter=self.request.user)

            BreakDownMove.objects.create(
                break_down = instance,
                user = self.request.user,
                status = 'RP',
            )


class BreakDownMakeMove(GenericAPIView):
    serializer_class = BreakDownMovePostSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        type = serializer.validated_data['type']
        break_down = serializer.validated_data['break_down']
        description = serializer.validated_data['description']

        obj = BreakDownMove.objects.create(
            break_down = break_down,
            status = type,
            user = self.request.user,
            description = description
        )
        return Response({"success": f"{obj.pk}"}, status=status.HTTP_201_CREATED)
    

