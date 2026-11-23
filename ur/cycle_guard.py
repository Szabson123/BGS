"""
UR::Cycle Guard – logika licznika cykli stanowiska.

Na komputerze stanowiska działają dwie aplikacje pilnujące się nawzajem:
- worker – co paczkę plików w kanale dolicza +1 do licznika kanału (zapis w pliku) i przenosi pliki,
- master – pobiera stąd konfigurację i polecenia, raportuje stan, pilnuje workera.

Stanowisko ma kanały: folder obserwowany + docelowy + BoxingCode, każdy z własnym licznikiem i limitem.
Backend jest źródłem konfiguracji i poleceń; liczniki żyją na stanowisku, tutaj trzymamy ich ostatni raport.
Serwis BGS-ur-eclipse-sync co minutę zgłasza NumQA z Eclipse – zmiana NumQA zeruje licznik kanału.
"""
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from .models import CycleChannel, CycleStation, CycleStationCommand, CycleStationEvent, hash_station_key

Event = CycleStationEvent.Kind


class CycleStationKeyAuthentication(BaseAuthentication):
    """Master przedstawia się kluczem stanowiska w nagłówku X-Api-Key (bez sesji użytkownika)."""

    def authenticate(self, request):
        raw_key = request.headers.get('X-Api-Key')
        if not raw_key:
            return None
        station = CycleStation.objects.filter(api_key_hash=hash_station_key(raw_key)).first()
        if station is None:
            raise AuthenticationFailed('Nieprawidłowy klucz stanowiska.')
        return AnonymousUser(), station

    def authenticate_header(self, request):
        return 'X-Api-Key'


class HasEclipseServiceKey(BasePermission):
    """Serwis BGS-ur-eclipse-sync – wspólny klucz z ustawień (pusty klucz = endpointy wyłączone)."""

    def has_permission(self, request, view):
        api_key = getattr(settings, 'CYCLE_GUARD_ECLIPSE_API_KEY', '')
        return bool(api_key) and hmac.compare_digest(request.headers.get('X-Api-Key', ''), api_key)


def normalize_boxing_code(code: str) -> str:
    return (code or '').strip().upper()


def is_online(station: CycleStation) -> bool:
    if not station.last_seen_at:
        return False
    offline_after = getattr(settings, 'CYCLE_GUARD_OFFLINE_SECONDS', 30)
    return (timezone.now() - station.last_seen_at).total_seconds() <= offline_after


def pending_commands(station: CycleStation):
    return station.commands.filter(acked_at__isnull=True).order_by('created_at')


def host_label(hostname: str, ip: str | None) -> str:
    if hostname and ip:
        return f'{hostname} ({ip})'
    return hostname or ip or 'nieznany komputer'


def _same_host(old_hostname: str, old_ip: str | None, new_hostname: str, new_ip: str | None) -> bool:
    """Nazwa komputera jest pewniejsza niż IP (DHCP) – IP porównujemy tylko, gdy nazwy brak."""
    if old_hostname and new_hostname:
        return old_hostname.lower() == new_hostname.lower()
    return not old_ip or not new_ip or old_ip == new_ip


def _check_host(station: CycleStation, hostname: str, ip: str | None, now) -> None:
    """
    Ten sam klucz z innego komputera:
    - poprzedni komputer raportował przed chwilą → klucz działa na dwóch komputerach naraz (KEY_CONFLICT),
    - poprzedni dawno milczy → stanowisko przeniesiono / wymieniono komputer (HOST_CHANGED).
    """
    if not (station.last_hostname or station.last_ip):
        return  # pierwsze zgłoszenie stanowiska
    if _same_host(station.last_hostname, station.last_ip, hostname, ip):
        return

    old = host_label(station.last_hostname, station.last_ip)
    new = host_label(hostname, ip)
    window = getattr(settings, 'CYCLE_GUARD_KEY_CONFLICT_SECONDS', 120)
    recently_seen = station.last_seen_at and (now - station.last_seen_at).total_seconds() <= window

    if recently_seen:
        # Przy konflikcie komputery zgłaszają się na zmianę co kilka sekund – jedno ostrzeżenie na 10 minut
        already_reported = station.events.filter(
            kind=Event.KEY_CONFLICT, created_at__gte=now - timedelta(minutes=10)
        ).exists()
        if not already_reported:
            CycleStationEvent.objects.create(
                station=station, kind=Event.KEY_CONFLICT,
                message=f'Klucz stanowiska zgłasza się z {new} i z {old} – ten sam klucz działa na dwóch komputerach.',
            )
    else:
        CycleStationEvent.objects.create(
            station=station, kind=Event.HOST_CHANGED,
            message=f'Stanowisko zgłasza się z nowego komputera: {new} (wcześniej {old}).',
        )


def _apply_channel_counter(channel: CycleChannel, current: int, last_cycle_at) -> None:
    previous = channel.counter
    channel.counter = current
    channel.last_cycle_at = last_cycle_at or channel.last_cycle_at
    channel.save(update_fields=['counter', 'last_cycle_at', 'updated_at'])
    if current <= previous:
        return
    step = f' (+{current - previous})' if current - previous > 1 else ''
    CycleStationEvent.objects.create(
        station=channel.station, channel=channel, kind=Event.CYCLE, counter=current,
        message=f'{channel.label}: cykl {current}/{channel.max_cycles}{step}',
    )
    if current >= channel.max_cycles > previous:
        CycleStationEvent.objects.create(
            station=channel.station, channel=channel, kind=Event.LIMIT_REACHED, counter=current,
            message=f'{channel.label}: osiągnięto limit {channel.max_cycles} cykli – koniec produkcji.',
        )


@transaction.atomic
def apply_agent_status(station: CycleStation, data: dict, ip: str | None = None) -> CycleStation:
    """Raport mastera: liczniki kanałów i stan workera. Wzrost licznika = nowe cykle w historii."""
    station = CycleStation.objects.select_for_update().get(pk=station.pk)
    now = timezone.now()
    hostname = (data.get('hostname') or '').strip()

    _check_host(station, hostname, ip, now)

    station.worker_alive = data['worker_alive']
    station.worker_paused = data['worker_paused']
    station.last_seen_at = now
    station.last_ip = ip or station.last_ip
    station.last_hostname = hostname or station.last_hostname
    station.save(update_fields=['worker_alive', 'worker_paused', 'last_seen_at', 'last_ip', 'last_hostname',
                                'updated_at'])

    # Kanały spoza stanowiska (np. usunięte w panelu, a worker jeszcze o nich nie wie) pomijamy
    channels = {channel.pk: channel for channel in station.channels.select_for_update()}
    for report in data.get('channels', []):
        channel = channels.get(report['id'])
        if channel is not None:
            _apply_channel_counter(channel, report['counter'], report.get('last_cycle_at'))
    return station


@transaction.atomic
def issue_command(station: CycleStation, command_type: str, user, channel: CycleChannel | None = None,
                  reason: str = '') -> tuple[CycleStationCommand, bool]:
    """Nowe polecenie dla mastera. Jeśli takie samo czeka jeszcze na wykonanie – nie dublujemy."""
    existing = pending_commands(station).filter(type=command_type, channel=channel).first()
    if existing:
        return existing, False
    command = CycleStationCommand.objects.create(station=station, type=command_type, channel=channel,
                                                 created_by=user)
    target = f' – {channel.label}' if channel else ''
    CycleStationEvent.objects.create(
        station=station, channel=channel, kind=Event.COMMAND_ISSUED, user=user,
        counter=channel.counter if channel else None,
        message=reason or f'Polecenie: {command.get_type_display()}{target}',
    )
    return command, True


def acknowledge_threat(event: CycleStationEvent, user) -> CycleStationEvent:
    if not event.acknowledged_at:
        event.acknowledged_at = timezone.now()
        event.acknowledged_by = user
        event.save(update_fields=['acknowledged_at', 'acknowledged_by', 'updated_at'])
    return event


@transaction.atomic
def ack_command(station: CycleStation, command: CycleStationCommand) -> CycleStationCommand:
    """Master potwierdza wykonanie polecenia (powtórne potwierdzenie nic nie zmienia)."""
    if command.acked_at:
        return command
    command.acked_at = timezone.now()
    command.save(update_fields=['acked_at', 'updated_at'])

    if command.type == CycleStationCommand.Type.RESET_COUNTER:
        channels = [command.channel] if command.channel_id else list(station.channels.all())
        for channel in channels:
            previous = channel.counter
            CycleChannel.objects.filter(pk=channel.pk).update(counter=0, updated_at=timezone.now())
            CycleStationEvent.objects.create(
                station=station, channel=channel, kind=Event.COUNTER_RESET, counter=0, user=command.created_by,
                message=f'{channel.label}: licznik wyzerowany (było {previous}/{channel.max_cycles}).',
            )
    elif command.type == CycleStationCommand.Type.RESTART_MASTER:
        CycleStationEvent.objects.create(
            station=station, kind=Event.MASTER_RESTARTED, user=command.created_by,
            message='Mistrz zrestartowany na polecenie z panelu.',
        )
    return command


# --- Serwis Eclipse (BGS-ur-eclipse-sync) ---

def eclipse_channels():
    """Kanały do odpytywania w Eclipse: z BoxingCode, na aktywnych stanowiskach."""
    return CycleChannel.objects.filter(station__is_active=True).exclude(boxing_code='').select_related('station')


@transaction.atomic
def apply_num_qa(boxing_code: str, num_qa: int) -> str:
    """
    Najnowszy NumQA dla BoxingCode z [Eclipse].[qav].[Batch].
    Pierwszy odczyt zapisuje punkt odniesienia; każdy inny NumQA niż poprzedni = nowa paczka QA → zerowanie kanału.
    Zwraca: baseline | unchanged | reset | unknown | inactive.
    """
    channel = (CycleChannel.objects.select_for_update().select_related('station')
               .filter(boxing_code=normalize_boxing_code(boxing_code)).first())
    if channel is None or not channel.boxing_code:
        return 'unknown'
    if not channel.station.is_active:
        return 'inactive'

    previous = channel.last_num_qa
    channel.last_num_qa = num_qa
    channel.num_qa_checked_at = timezone.now()
    channel.save(update_fields=['last_num_qa', 'num_qa_checked_at', 'updated_at'])

    if previous is None:
        return 'baseline'
    if previous == num_qa:
        return 'unchanged'

    CycleStationEvent.objects.create(
        station=channel.station, channel=channel, kind=Event.QA_BATCH_CHANGED, counter=channel.counter,
        message=f'{channel.label}: nowa paczka QA w Eclipse (NumQA {previous} → {num_qa}) – zerowanie licznika.',
    )
    issue_command(channel.station, CycleStationCommand.Type.RESET_COUNTER, None, channel=channel,
                  reason=f'Polecenie: Zerowanie licznika – {channel.label} (Eclipse)')
    return 'reset'
