"""
UR::Cycle Guard – logika licznika cykli stanowiska.

Na komputerze stanowiska działają dwie aplikacje pilnujące się nawzajem:
- worker – co paczkę plików dolicza +1 do licznika (zapis w pliku) i przenosi pliki,
- master – pobiera stąd konfigurację i polecenia, raportuje stan, pilnuje workera.

Backend jest źródłem konfiguracji i poleceń; licznik żyje na stanowisku, tutaj trzymamy jego ostatni raport.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import CycleStation, CycleStationCommand, CycleStationEvent, hash_station_key

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
                station=station, kind=Event.KEY_CONFLICT, counter=station.counter,
                message=f'Klucz stanowiska zgłasza się z {new} i z {old} – ten sam klucz działa na dwóch komputerach.',
            )
    else:
        CycleStationEvent.objects.create(
            station=station, kind=Event.HOST_CHANGED, counter=station.counter,
            message=f'Stanowisko zgłasza się z nowego komputera: {new} (wcześniej {old}).',
        )


@transaction.atomic
def apply_agent_status(station: CycleStation, data: dict, ip: str | None = None) -> CycleStation:
    """Raport mastera: aktualny licznik i stan workera. Wzrost licznika = nowe cykle w historii."""
    station = CycleStation.objects.select_for_update().get(pk=station.pk)
    now = timezone.now()
    previous = station.counter
    current = data['counter']
    hostname = (data.get('hostname') or '').strip()

    _check_host(station, hostname, ip, now)

    station.counter = current
    station.worker_alive = data['worker_alive']
    station.worker_paused = data['worker_paused']
    station.last_cycle_at = data.get('last_cycle_at') or station.last_cycle_at
    station.last_seen_at = now
    station.last_ip = ip or station.last_ip
    station.last_hostname = hostname or station.last_hostname
    station.save(update_fields=['counter', 'worker_alive', 'worker_paused', 'last_cycle_at',
                                'last_seen_at', 'last_ip', 'last_hostname', 'updated_at'])

    if current > previous:
        CycleStationEvent.objects.create(
            station=station, kind=Event.CYCLE, counter=current,
            message=f'Cykl {current}/{station.max_cycles}' + (f' (+{current - previous})' if current - previous > 1 else ''),
        )
        if current >= station.max_cycles > previous:
            CycleStationEvent.objects.create(
                station=station, kind=Event.LIMIT_REACHED, counter=current,
                message=f'Osiągnięto limit {station.max_cycles} cykli – koniec produkcji.',
            )
    return station


@transaction.atomic
def issue_command(station: CycleStation, command_type: str, user) -> tuple[CycleStationCommand, bool]:
    """Nowe polecenie dla mastera. Jeśli takie samo czeka jeszcze na wykonanie – nie dublujemy."""
    existing = pending_commands(station).filter(type=command_type).first()
    if existing:
        return existing, False
    command = CycleStationCommand.objects.create(station=station, type=command_type, created_by=user)
    CycleStationEvent.objects.create(
        station=station, kind=Event.COMMAND_ISSUED, user=user, counter=station.counter,
        message=f'Polecenie: {command.get_type_display()}',
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
        previous = station.counter
        CycleStation.objects.filter(pk=station.pk).update(counter=0, updated_at=timezone.now())
        CycleStationEvent.objects.create(
            station=station, kind=Event.COUNTER_RESET, counter=0, user=command.created_by,
            message=f'Licznik wyzerowany (było {previous}/{station.max_cycles}).',
        )
    elif command.type == CycleStationCommand.Type.RESTART_MASTER:
        CycleStationEvent.objects.create(
            station=station, kind=Event.MASTER_RESTARTED, user=command.created_by,
            message='Mistrz zrestartowany na polecenie z panelu.',
        )
    return command
