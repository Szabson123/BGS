from django.db import transaction
from rest_framework import serializers

from .cycle_guard import is_online, normalize_boxing_code
from .models import CycleChannel, CycleStation, CycleStationCommand, CycleStationEvent


def _folder_key(path: str) -> str:
    return (path or '').strip().rstrip('\\/').lower()


class CycleChannelSerializer(serializers.ModelSerializer):
    # id przy zapisie stanowiska: jest = zmiana istniejącego kanału, brak = nowy kanał
    id = serializers.IntegerField(required=False)
    label = serializers.CharField(read_only=True)
    limit_reached = serializers.BooleanField(read_only=True)
    boxing_code = serializers.CharField(max_length=64)

    class Meta:
        model = CycleChannel
        fields = ['id', 'label', 'name', 'boxing_code', 'watch_dir', 'dest_dir', 'max_cycles',
                  'counter', 'limit_reached', 'last_cycle_at', 'last_num_qa', 'num_qa_checked_at']
        read_only_fields = ['counter', 'last_cycle_at', 'last_num_qa', 'num_qa_checked_at']

    def validate_boxing_code(self, value):
        code = normalize_boxing_code(value)
        if not code:
            raise serializers.ValidationError('Podaj BoxingCode.')
        return code

    def validate_max_cycles(self, value):
        if value < 1:
            raise serializers.ValidationError('Limit cykli musi wynosić co najmniej 1.')
        return value

    def validate(self, attrs):
        if _folder_key(attrs['watch_dir']) == _folder_key(attrs['dest_dir']):
            raise serializers.ValidationError({'dest_dir': 'Folder docelowy musi być inny niż obserwowany.'})
        return attrs


class CycleStationSerializer(serializers.ModelSerializer):
    machine_name = serializers.CharField(source='machine.name', read_only=True, default=None)
    channels = CycleChannelSerializer(many=True)
    limit_reached = serializers.BooleanField(read_only=True)
    online = serializers.SerializerMethodField()
    pending_commands = serializers.SerializerMethodField()
    active_threats = serializers.SerializerMethodField()

    class Meta:
        model = CycleStation
        fields = [
            'id', 'name', 'machine', 'machine_name',
            'channels', 'settle_seconds', 'tester_process_name', 'is_active', 'config_version',
            'limit_reached', 'worker_alive', 'worker_paused', 'last_seen_at', 'last_ip', 'last_hostname',
            'online', 'pending_commands', 'active_threats', 'created_at', 'updated_at',
        ]
        read_only_fields = ['config_version', 'worker_alive', 'worker_paused',
                            'last_seen_at', 'last_ip', 'last_hostname', 'created_at', 'updated_at']

    def get_online(self, obj) -> bool:
        return is_online(obj)

    def get_active_threats(self, obj) -> int:
        annotated = getattr(obj, 'active_threat_count', None)
        if annotated is not None:
            return annotated
        return obj.events.filter(kind__in=CycleStationEvent.THREAT_KINDS, acknowledged_at__isnull=True).count()

    def get_pending_commands(self, obj) -> list[dict]:
        commands = getattr(obj, 'pending_command_list', None)
        if commands is None:
            commands = obj.commands.filter(acked_at__isnull=True)
        return [{'type': command.type, 'channel': command.channel_id} for command in commands]

    def validate_settle_seconds(self, value):
        if value < 5:
            raise serializers.ValidationError('Minimalnie 5 sekund.')
        return value

    def validate_channels(self, channels):
        if not channels:
            raise serializers.ValidationError('Stanowisko musi mieć co najmniej jeden kanał.')

        own_ids = set(self.instance.channels.values_list('id', flat=True)) if self.instance else set()
        errors, codes, folders = [{} for _ in channels], {}, {}
        for index, channel in enumerate(channels):
            if 'id' in channel and channel['id'] not in own_ids:
                errors[index]['id'] = 'Kanał nie należy do tego stanowiska.'
            code = channel['boxing_code']
            if code in codes:
                errors[index]['boxing_code'] = f'BoxingCode powtórzony (kanał {codes[code] + 1}).'
            codes.setdefault(code, index)
            # Folder obserwowany nie może być docelowym ani obserwowanym innego kanału – pliki krążyłyby w kółko
            for field in ('watch_dir', 'dest_dir'):
                key = _folder_key(channel[field])
                other = folders.get(key)
                if other is not None and (field == 'watch_dir' or other[1] == 'watch_dir'):
                    errors[index][field] = f'Folder używany już w kanale {other[0] + 1}.'
                folders.setdefault(key, (index, field))

        taken = (CycleChannel.objects.filter(boxing_code__in=codes).exclude(station=self.instance)
                 .select_related('station') if self.instance else
                 CycleChannel.objects.filter(boxing_code__in=codes).select_related('station'))
        for channel in taken:
            errors[codes[channel.boxing_code]]['boxing_code'] = (
                f'BoxingCode jest już przypisany do stanowiska {channel.station.name}.')

        if any(errors):
            raise serializers.ValidationError(errors)
        return channels

    @transaction.atomic
    def create(self, validated_data):
        channels = validated_data.pop('channels')
        station = CycleStation(**validated_data)
        raw_key = station.set_new_api_key()
        station.save()
        for channel in channels:
            channel.pop('id', None)
            CycleChannel.objects.create(station=station, **channel)
        station.raw_api_key = raw_key  # widoczny tylko w odpowiedzi na utworzenie
        return station

    def _save_channels(self, station: CycleStation, channels: list[dict]) -> list[str]:
        """Lista kanałów z panelu zastępuje obecną: nowe dodajemy, brakujące usuwamy. Zwraca opis zmian."""
        existing = {channel.pk: channel for channel in station.channels.all()}
        incoming_ids = {channel['id'] for channel in channels if 'id' in channel}
        changes = []

        for pk, channel in existing.items():
            if pk not in incoming_ids:
                changes.append(f'usunięty kanał {channel.label}')
                channel.delete()

        # Zmieniane kody najpierw zwalniamy – zamiana BoxingCode między kanałami nie łamie unikalności
        recoded = [data['id'] for data in channels
                   if 'id' in data and data['boxing_code'] != existing[data['id']].boxing_code]
        CycleChannel.objects.filter(pk__in=recoded).update(boxing_code='')

        for data in channels:
            pk = data.pop('id', None)
            if pk is None:
                channel = CycleChannel.objects.create(station=station, **data)
                changes.append(f'nowy kanał {channel.label}')
                continue
            channel = existing[pk]
            changed = [field for field in CycleChannel.CONFIG_FIELDS if data[field] != getattr(channel, field)]
            if not changed:
                continue
            if 'boxing_code' in changed:
                # Inny BoxingCode = inna paczka w Eclipse – pierwszy odczyt NumQA ustawi nowy punkt odniesienia
                channel.last_num_qa = None
                channel.num_qa_checked_at = None
            for field in changed:
                setattr(channel, field, data[field])
            channel.save()
            changes.append(f'{channel.label}: ' + ', '.join(changed))
        return changes

    @transaction.atomic
    def update(self, instance, validated_data):
        channels = validated_data.pop('channels', None)
        changed = [field for field in CycleStation.CONFIG_FIELDS
                   if field in validated_data and validated_data[field] != getattr(instance, field)]
        station = super().update(instance, validated_data)
        if channels is not None:
            changed += self._save_channels(station, channels)
        if changed:
            station.config_version += 1
            station.save(update_fields=['config_version', 'updated_at'])
            request = self.context.get('request')
            CycleStationEvent.objects.create(
                station=station, kind=CycleStationEvent.Kind.CONFIG_CHANGED,
                user=request.user if request else None,
                message=('Zmienione: ' + '; '.join(changed))[:500],
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
        fields = ['id', 'kind', 'kind_display', 'is_threat', 'channel', 'message', 'counter', 'user_name',
                  'created_at', 'acknowledged_at', 'acknowledged_by_name']

    def get_user_name(self, obj) -> str | None:
        return _display_name(obj.user)

    def get_acknowledged_by_name(self, obj) -> str | None:
        return _display_name(obj.acknowledged_by)


class CycleStationCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = CycleStationCommand
        # channel: null = polecenie dla całego stanowiska (zerowanie wszystkich kanałów)
        fields = ['id', 'type', 'channel', 'created_at', 'acked_at']


# --- Agent (master) ---

class AgentChannelConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CycleChannel
        fields = ['id', 'label', 'boxing_code', 'watch_dir', 'dest_dir', 'max_cycles']


class AgentConfigSerializer(serializers.ModelSerializer):
    channels = AgentChannelConfigSerializer(many=True, read_only=True)

    class Meta:
        model = CycleStation
        fields = ['id', 'name', 'config_version', 'channels', 'settle_seconds', 'tester_process_name', 'is_active']


class AgentChannelStatusSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    counter = serializers.IntegerField(min_value=0)
    last_cycle_at = serializers.DateTimeField(required=False, allow_null=True)


class AgentStatusSerializer(serializers.Serializer):
    channels = AgentChannelStatusSerializer(many=True)
    worker_alive = serializers.BooleanField()
    worker_paused = serializers.BooleanField()
    # Nazwa komputera stanowiska (socket.gethostname()) – IP backend bierze z połączenia
    hostname = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class AgentEventSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[(kind.value, kind.label) for kind in CycleStationEvent.AGENT_KINDS])
    message = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


# --- Serwis Eclipse (BGS-ur-eclipse-sync) ---

class EclipseChannelSerializer(serializers.ModelSerializer):
    station_name = serializers.CharField(source='station.name', read_only=True)

    class Meta:
        model = CycleChannel
        fields = ['id', 'station_name', 'boxing_code', 'last_num_qa', 'num_qa_checked_at']


class EclipseNumQaItemSerializer(serializers.Serializer):
    boxing_code = serializers.CharField(max_length=64)
    num_qa = serializers.IntegerField()


class EclipseNumQaSerializer(serializers.Serializer):
    results = EclipseNumQaItemSerializer(many=True)
