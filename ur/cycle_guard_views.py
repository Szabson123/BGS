from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .cycle_guard import CycleStationKeyAuthentication, ack_command, apply_agent_status, issue_command, pending_commands
from .cycle_guard_serializers import (AgentConfigSerializer, AgentEventSerializer, AgentStatusSerializer,
                                      CycleStationCommandSerializer, CycleStationEventSerializer, CycleStationSerializer)
from .models import CycleStation, CycleStationCommand, CycleStationEvent
from .permissions import CycleGuardPermission, HasCycleStationKey

EVENTS_DEFAULT_LIMIT = 100
EVENTS_MAX_LIMIT = 500


# --- Panel (frontend) ---

class CycleStationViewSet(viewsets.ModelViewSet):
    """
    Stanowiska licznika cykli. Podgląd – każdy z dostępem do BGS::UR;
    konfiguracja i polecenia – admin UR / inżynier procesu (ur_process_engineer).
    """
    serializer_class = CycleStationSerializer
    permission_classes = [CycleGuardPermission]

    def get_queryset(self):
        return CycleStation.objects.select_related('machine').prefetch_related(
            Prefetch('commands', queryset=CycleStationCommand.objects.filter(acked_at__isnull=True),
                     to_attr='pending_command_list')
        )

    def _command(self, request, command_type):
        station = self.get_object()
        command, created = issue_command(station, command_type, request.user)
        return Response(CycleStationCommandSerializer(command).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reset-counter')
    def reset_counter(self, request, pk=None):
        """Zerowanie licznika – wykona master przy najbliższym odpytaniu."""
        return self._command(request, CycleStationCommand.Type.RESET_COUNTER)

    @action(detail=True, methods=['post'], url_path='restart-master')
    def restart_master(self, request, pk=None):
        """Reset mistrza – master potwierdza polecenie i uruchamia się ponownie."""
        return self._command(request, CycleStationCommand.Type.RESTART_MASTER)

    @action(detail=True, methods=['post'], url_path='regenerate-key')
    def regenerate_key(self, request, pk=None):
        """Nowy klucz agenta (stary przestaje działać). Jawny klucz zwracany tylko tutaj."""
        station = self.get_object()
        raw_key = station.set_new_api_key()
        station.save(update_fields=['api_key_hash', 'updated_at'])
        return Response({'api_key': raw_key})

    @action(detail=True, methods=['get'])
    def events(self, request, pk=None):
        station = self.get_object()
        try:
            limit = min(int(request.query_params.get('limit', EVENTS_DEFAULT_LIMIT)), EVENTS_MAX_LIMIT)
        except ValueError:
            limit = EVENTS_DEFAULT_LIMIT
        events = station.events.select_related('user')[:max(limit, 1)]
        return Response(CycleStationEventSerializer(events, many=True).data)


# --- Agent (aplikacja master na stanowisku, klucz X-Api-Key) ---

class AgentHealthView(APIView):
    """Ping dostępności backendu – bez klucza, żeby master odróżnił 'backend leży' od 'zły klucz'."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok', 'server_time': timezone.now()})


class AgentBaseView(APIView):
    authentication_classes = [CycleStationKeyAuthentication]
    permission_classes = [HasCycleStationKey]

    @property
    def station(self) -> CycleStation:
        return self.request.auth


class AgentConfigView(AgentBaseView):
    def get(self, request):
        return Response(AgentConfigSerializer(self.station).data)


class AgentStatusView(AgentBaseView):
    """Raport stanu. W odpowiedzi wersja konfiguracji i oczekujące polecenia – jedno zapytanie na cykl."""
    serializer_class = AgentStatusSerializer

    def post(self, request):
        serializer = AgentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        station = apply_agent_status(self.station, serializer.validated_data)
        return Response({
            'config_version': station.config_version,
            'limit_reached': station.limit_reached,
            'commands': CycleStationCommandSerializer(pending_commands(station), many=True).data,
        })


class AgentCommandsView(AgentBaseView):
    def get(self, request):
        return Response(CycleStationCommandSerializer(pending_commands(self.station), many=True).data)


class AgentCommandAckView(AgentBaseView):
    def post(self, request, command_id):
        command = get_object_or_404(CycleStationCommand, pk=command_id, station=self.station)
        command = ack_command(self.station, command)
        return Response(CycleStationCommandSerializer(command).data)


class AgentEventView(AgentBaseView):
    """Zdarzenia znane tylko stanowisku: restart workera, niedostępny backend, zamknięty tester, błędy."""
    serializer_class = AgentEventSerializer

    def post(self, request):
        serializer = AgentEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = CycleStationEvent.objects.create(
            station=self.station, counter=self.station.counter, **serializer.validated_data
        )
        return Response(CycleStationEventSerializer(event).data, status=status.HTTP_201_CREATED)
