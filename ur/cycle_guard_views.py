from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .cycle_guard import (CycleStationKeyAuthentication, HasEclipseServiceKey, ack_command, acknowledge_threat,
                          apply_agent_status, apply_num_qa, eclipse_channels, issue_command, pending_commands)
from .cycle_guard_serializers import (AgentConfigSerializer, AgentEventSerializer, AgentStatusSerializer,
                                      CycleStationCommandSerializer, CycleStationEventSerializer, CycleStationSerializer,
                                      EclipseChannelSerializer, EclipseNumQaSerializer)
from .models import CycleChannel, CycleStation, CycleStationCommand, CycleStationEvent
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
            'channels',
            Prefetch('commands', queryset=CycleStationCommand.objects.filter(acked_at__isnull=True),
                     to_attr='pending_command_list')
        ).annotate(active_threat_count=Count(
            'events', filter=Q(events__kind__in=CycleStationEvent.THREAT_KINDS, events__acknowledged_at__isnull=True)
        ))

    def _command(self, request, command_type, channel=None):
        station = self.get_object()
        command, created = issue_command(station, command_type, request.user, channel=channel)
        return Response(CycleStationCommandSerializer(command).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reset-counter')
    def reset_counter(self, request, pk=None):
        """Zerowanie licznika – wykona master przy najbliższym odpytaniu. {"channel": id} – jeden kanał, bez – wszystkie."""
        channel = None
        channel_id = request.data.get('channel')
        if channel_id not in (None, ''):
            channel = get_object_or_404(CycleChannel, pk=channel_id, station_id=pk)
        return self._command(request, CycleStationCommand.Type.RESET_COUNTER, channel)

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
        events = station.events.select_related('user', 'acknowledged_by')[:max(limit, 1)]
        return Response(CycleStationEventSerializer(events, many=True).data)

    @action(detail=True, methods=['get'])
    def threats(self, request, pk=None):
        """Zagrożenia stanowiska – domyślnie tylko niepotwierdzone (?all=1 – także potwierdzone)."""
        station = self.get_object()
        threats = station.events.select_related('user', 'acknowledged_by').filter(
            kind__in=CycleStationEvent.THREAT_KINDS
        )
        if request.query_params.get('all') not in ('1', 'true'):
            threats = threats.filter(acknowledged_at__isnull=True)
        return Response(CycleStationEventSerializer(threats[:EVENTS_MAX_LIMIT], many=True).data)

    @action(detail=True, methods=['post'], url_path=r'threats/(?P<event_id>\d+)/acknowledge')
    def acknowledge_threat(self, request, pk=None, event_id=None):
        """Potwierdzenie zagrożenia – znika z listy aktywnych, zostaje w historii z informacją kto i kiedy."""
        station = self.get_object()
        event = get_object_or_404(station.events, pk=event_id, kind__in=CycleStationEvent.THREAT_KINDS)
        return Response(CycleStationEventSerializer(acknowledge_threat(event, request.user)).data)


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
        # REMOTE_ADDR – w sieci lokalnej bez proxy to faktyczny adres stanowiska
        station = apply_agent_status(self.station, serializer.validated_data, ip=request.META.get('REMOTE_ADDR'))
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
        event = CycleStationEvent.objects.create(station=self.station, **serializer.validated_data)
        return Response(CycleStationEventSerializer(event).data, status=status.HTTP_201_CREATED)


# --- Serwis Eclipse (BGS-ur-eclipse-sync, klucz CYCLE_GUARD_ECLIPSE_API_KEY) ---

class EclipseBaseView(APIView):
    authentication_classes = []
    permission_classes = [HasEclipseServiceKey]


class EclipseChannelsView(EclipseBaseView):
    """BoxingCode do odpytania w [Eclipse].[qav].[Batch] razem z ostatnio znanym NumQA."""

    def get(self, request):
        return Response(EclipseChannelSerializer(eclipse_channels(), many=True).data)


class EclipseNumQaView(EclipseBaseView):
    """Najnowsze NumQA z jednego odpytania Eclipse. Zmiana NumQA = zerowanie licznika kanału."""
    serializer_class = EclipseNumQaSerializer

    def post(self, request):
        serializer = EclipseNumQaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = {item['boxing_code']: apply_num_qa(item['boxing_code'], item['num_qa'])
                   for item in serializer.validated_data['results']}
        return Response({'results': results})
