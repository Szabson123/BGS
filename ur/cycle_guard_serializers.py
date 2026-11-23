from rest_framework import serializers

from .cycle_guard import is_online
from .models import CycleStation, CycleStationCommand, CycleStationEvent


class CycleStationSerializer(serializers.ModelSerializer):
    machine_name = serializers.CharField(source='machine.name', read_only=True, default=None)
    limit_reached = serializers.BooleanField(read_only=True)
    online = serializers.SerializerMethodField()
    pending_commands = serializers.SerializerMethodField()
    active_threats = serializers.SerializerMethodField()

    class Meta:
        model = CycleStation
        fields = [
            'id', 'name', 'machine', 'machine_name',
            'watch_dir', 'dest_dir', 'max_cycles', 'settle_seconds', 'tester_process_name', 'is_active',
            'config_version',
            'counter', 'limit_reached', 'worker_alive', 'worker_paused', 'last_cycle_at', 'last_seen_at',
            'last_ip', 'last_hostname',
            'online', 'pending_commands', 'active_threats', 'created_at', 'updated_at',
        ]
        read_only_fields = ['config_version', 'counter', 'worker_alive', 'worker_paused',
                            'last_cycle_at', 'last_seen_at', 'last_ip', 'last_hostname', 'created_at', 'updated_at']

    def get_online(self, obj) -> bool:
        return is_online(obj)

    def get_active_threats(self, obj) -> int:
        annotated = getattr(obj, 'active_threat_count', None)
        if annotated is not None:
            return annotated
        return obj.events.filter(kind__in=CycleStationEvent.THREAT_KINDS, acknowledged_at__isnull=True).count()

    def get_pending_commands(self, obj) -> list[str]:
        commands = getattr(obj, 'pending_command_list', None)
        if commands is None:
            commands = obj.commands.filter(acked_at__isnull=True)
        return [command.type for command in commands]

    def validate_max_cycles(self, value):
        if value < 1:
            raise serializers.ValidationError('Limit cykli musi wynosić co najmniej 1.')
        return value

    def validate_settle_seconds(self, value):
        if value < 5:
            raise serializers.ValidationError('Minimalnie 5 sekund.')
        return value

    def validate(self, attrs):
        watch_dir = attrs.get('watch_dir', getattr(self.instance, 'watch_dir', None))
        dest_dir = attrs.get('dest_dir', getattr(self.instance, 'dest_dir', None))
        if watch_dir and dest_dir and watch_dir.strip().rstrip('\\/').lower() == dest_dir.strip().rstrip('\\/').lower():
            raise serializers.ValidationError({'dest_dir': 'Folder docelowy musi być inny niż obserwowany.'})
        return attrs

    def create(self, validated_data):
        station = CycleStation(**validated_data)
        raw_key = station.set_new_api_key()
        station.save()
        station.raw_api_key = raw_key  # widoczny tylko w odpowiedzi na utworzenie
        return station

    def update(self, instance, validated_data):
        changed = [field for field in CycleStation.CONFIG_FIELDS
                   if field in validated_data and validated_data[field] != getattr(instance, field)]
        station = super().update(instance, validated_data)
        if changed:
            station.config_version += 1
            station.save(update_fields=['config_version', 'updated_at'])
            request = self.context.get('request')
            CycleStationEvent.objects.create(
                station=station, kind=CycleStationEvent.Kind.CONFIG_CHANGED,
                user=request.user if request else None, counter=station.counter,
                message='Zmienione: ' + ', '.join(changed),
            )
        return station

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if getattr(instance, 'raw_api_key', None):
            data['api_key'] = instance.raw_api_key
        return data


def _display_name(user) -> str | None:
    if not user:
        return None
    return f'{user.first_name} {user.last_name}'.strip() or user.username


class CycleStationEventSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    is_threat = serializers.BooleanField(read_only=True)
    user_name = serializers.SerializerMethodField()
    acknowledged_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CycleStationEvent
        fields = ['id', 'kind', 'kind_display', 'is_threat', 'message', 'counter', 'user_name', 'created_at',
                  'acknowledged_at', 'acknowledged_by_name']

    def get_user_name(self, obj) -> str | None:
        return _display_name(obj.user)

    def get_acknowledged_by_name(self, obj) -> str | None:
        return _display_name(obj.acknowledged_by)


class CycleStationCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = CycleStationCommand
        fields = ['id', 'type', 'created_at', 'acked_at']


# --- Agent (master) ---

class AgentConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CycleStation
        fields = ['id', 'name', 'config_version', 'watch_dir', 'dest_dir', 'max_cycles',
                  'settle_seconds', 'tester_process_name', 'is_active']


class AgentStatusSerializer(serializers.Serializer):
    counter = serializers.IntegerField(min_value=0)
    worker_alive = serializers.BooleanField()
    worker_paused = serializers.BooleanField()
    last_cycle_at = serializers.DateTimeField(required=False, allow_null=True)
    # Nazwa komputera stanowiska (socket.gethostname()) – IP backend bierze z połączenia
    hostname = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class AgentEventSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[(kind.value, kind.label) for kind in CycleStationEvent.AGENT_KINDS])
    message = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
