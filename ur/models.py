import hashlib
import secrets

from django.db import models
from django.db.models import Prefetch, Window, F
from user.models import CustomUser
from django.db.models.functions import RowNumber


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class MachineQuerySet(models.QuerySet):
    def with_full_history(self, workshop=None):
        if workshop:
            queryset = self.select_related('department').prefetch_related(
                Prefetch(
                    'breakdowns',
                    queryset=Breakdown.objects.select_related('reporter').prefetch_related(
                        Prefetch(
                            'history',
                            queryset=BreakdownMove.objects.select_related('user')
                        )
                    ).filter(department__workshop=workshop).order_by('-created_at')
                )
            )
            return queryset 
        else:
            return self.none()
    
class BreakdownQuerySet(models.QuerySet):
    def with_last_status(self):
        statuses = BreakdownMove.objects.select_related('user').annotate(
            row_number=Window( # Deklaracja Wiaderka
                expression=RowNumber(), # Deklaracja że będziemy numerować rzędy w "Wiaderku"
                partition_by=F('breakdown_id'), # Liczby będą niezależne od obiektu Breakdown czyli bedziemy restować nasze liczby co Breakdown
                order_by=F('created_at').desc()
            )
        ).filter(row_number=1) # Wycinamy wszystko oprócz naszej jedynki 2. W moim Django 6.0 działa natywnie w Postgresql

        return (self.select_related('reporter', 'machine')
                    .prefetch_related(
                        Prefetch(
                            'history', queryset=statuses,
                            to_attr='current_status_list'
                        )
                    )
                )


class Workshop(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    owner = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return self.name
    
# Komentarz -> kiedy jestes participantem to możesz awarie modyfikować jesli nie jestes to nie mozesz, curentworshop jest tylko do odczytu i zgłaszania awari

class WorkshopParticipant(BaseModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='workshopparticipant')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='workshopparticipant')


class CurrentWorkshop(BaseModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='currentworkshop')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='currentworkshop')


class Department(BaseModel):
    name = models.CharField(max_length=255)


class CurrentDepartment(BaseModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='currentdepartments')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='currentdepartments')

    class Meta:
        unique_together = ('user', 'department')

    def __str__(self):
        return f"{self.user.username} -> {self.department.name}"


class MachineLineName(BaseModel):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


# Komentarz -> zespół to stała jednostka między supervisorem a pracownikami produkcji.
# Supervisor widzi pracowników ze swoich zespołów; gdy odchodzi, przekazuje się jego zespoły innej osobie.

class ProductionTeam(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    description = models.CharField(max_length=500, null=True, blank=True)
    supervisors = models.ManyToManyField(CustomUser, related_name='supervised_production_teams', blank=True)
    members = models.ManyToManyField(CustomUser, related_name='production_teams', blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Machine(BaseModel):
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, null=True, blank=True, related_name='machines')
    line = models.ForeignKey(MachineLineName, on_delete=models.SET_NULL, null=True, blank=True, related_name='lines')
    name = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, null=True, blank=True)
    phase_id = models.CharField(max_length=255, null=True, blank=True)
    sigip_num = models.CharField(max_length=255, null=True, blank=True)

    objects = MachineQuerySet.as_manager()

    def __str__(self):
        return self.name


class MachineModel(BaseModel):
    name = models.CharField(max_length=255)
    machine = models.OneToOneField(Machine, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name


class WorkSchedulePreset(BaseModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, null=True, blank=True, related_name='schedules')
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=500, null=True, blank=True)
    shift_duration_hours = models.PositiveIntegerField(default=8)
    is_active = models.BooleanField(default=False)

    def __str__(self):
        machine_name = self.machine.name if self.machine else 'Globalny'
        return f"{machine_name} - {self.name} ({self.shift_duration_hours}h)"


class ScheduleBreak(BaseModel):
    preset = models.ForeignKey(WorkSchedulePreset, on_delete=models.CASCADE, related_name='breaks')
    name = models.CharField(max_length=255)
    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=15)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['start_time', 'order']

    def __str__(self):
        return f"{self.preset.name} - {self.name} ({self.start_time} +{self.duration_minutes}m)"
    

class ClosingBreakdownTypes(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)


class ResponsibleForBreakdown(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    

class MachineNotes(BaseModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='notes')
    description = models.CharField(max_length=10000, null=True, blank=True)
    file = models.FileField(null=True, blank=True, upload_to='machine/notes')
    created_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE)


class MachineNoteFile(BaseModel):
    note = models.ForeignKey(MachineNotes, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to='machine/notes')
    file_name = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"{self.note_id} - {self.file_name or self.file.name}"


class Breakdown(BaseModel):
    class Priority(models.TextChoices):
        NONE = 'NONE', 'Brak'
        MID = 'MID', 'Średni'
        HIGH = 'HIGH', 'Wysoki'

    DESCRIPTION_MAX_LENGTH = 4000

    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='breakdowns')
    priority = models.CharField(max_length=4, choices=Priority, default=Priority.NONE)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='breakdowns')
    description = models.CharField(max_length=DESCRIPTION_MAX_LENGTH, null=True, blank=True)
    objects = BreakdownQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.machine.name} {self.created_at}"
    

# Komentarz -> anulowana awaria znika z systemu, a jej stan z chwili anulowania zostaje tutaj

class CancelledBreakdownSnapshot(BaseModel):
    original_breakdown_id = models.PositiveIntegerField()
    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_breakdowns')
    machine_name = models.CharField(max_length=255)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='reported_cancelled_breakdowns')
    priority = models.CharField(max_length=4)
    description = models.TextField(null=True, blank=True)
    reported_at = models.DateTimeField()
    cancelled_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='cancelled_breakdowns')
    history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Anulowana #{self.original_breakdown_id} – {self.machine_name}"


class AdditionalEndingBreakdownInfo(BaseModel):
    breakdown = models.OneToOneField(Breakdown, on_delete=models.CASCADE, related_name='additional')
    closing_breakdown_type = models.ForeignKey(ClosingBreakdownTypes, on_delete=models.CASCADE)
    responsible_for_breakdown = models.ForeignKey(ResponsibleForBreakdown, on_delete=models.CASCADE)
    

class BreakdownMove(BaseModel):
    class Status(models.TextChoices):
        REPORTED = 'RP', 'Zgłoszony'
        STARTED = 'ST', 'W Naprawie'
        ENDED = 'ED', 'Zakończony'

    DESCRIPTION_MAX_LENGTH = 4000

    breakdown = models.ForeignKey(Breakdown, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=2, choices=Status)
    description = models.CharField(max_length=DESCRIPTION_MAX_LENGTH, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['breakdown', '-created_at']),
        ]

    def __str__(self):
        return f"{self.breakdown.machine.name} - {self.status} {self.created_at}"
    

class MachineInspection(BaseModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='inspections')
    date = models.DateTimeField()
    responsible = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    responsible_text = models.CharField(max_length=255)


class MachineInspectionMoveDateLog(BaseModel):
    machine_inspection = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='logs')
    who = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    old_date = models.DateTimeField()


class WorkshopInspectionsPreset(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.SET_NULL)


class WorkshopInspectionsPresetField(BaseModel):
    class AvaibleTypes(models.TextChoices):
        BOOL = 'TF', 'True/False'
    preset = models.ForeignKey(WorkshopInspectionsPreset, on_delete=models.CASCADE)
    text = models.CharField(max_length=255)
    type = models.CharField(choices=AvaibleTypes, max_length=5)


def hash_station_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


class CycleStation(BaseModel):
    CONFIG_FIELDS = ('settle_seconds', 'tester_process_name', 'is_active')

    name = models.CharField(max_length=255, unique=True)
    # Powiązanie z maszyną UR jest opcjonalne – stanowisko może istnieć samodzielnie
    machine = models.OneToOneField(Machine, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='cycle_station')

    # Konfiguracja wysyłana do mastera (każda zmiana – też kanałów – podbija config_version)
    settle_seconds = models.PositiveIntegerField(default=60)  # paczka "kompletna" po tylu sekundach bez zmian
    tester_process_name = models.CharField(max_length=255, blank=True, default='')  # np. Tester.exe
    is_active = models.BooleanField(default=True)
    config_version = models.PositiveIntegerField(default=1)

    # Klucz agenta – trzymamy tylko skrót, jawny klucz widać raz przy tworzeniu / regeneracji
    api_key_hash = models.CharField(max_length=64, unique=True)

    # Stan raportowany przez mastera (liczniki są w kanałach)
    worker_alive = models.BooleanField(default=False)
    worker_paused = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # Skąd ostatnio zgłosił się master – tylko informacyjnie, klucz nie jest przypisany do IP (DHCP)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_hostname = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def limit_reached(self) -> bool:
        """Wystarczy jeden kanał na limicie – staje całe stanowisko (tester jest jeden)."""
        return any(channel.limit_reached for channel in self.channels.all())

    def set_new_api_key(self) -> str:
        raw_key = secrets.token_urlsafe(32)
        self.api_key_hash = hash_station_key(raw_key)
        return raw_key


class CycleChannel(BaseModel):
    """
    Jedno miejsce, do którego loguje stanowisko: folder obserwowany + docelowy + BoxingCode z Eclipse.
    Każdy kanał ma własny licznik i limit; zmiana NumQA dla BoxingCode w Eclipse zeruje licznik kanału.
    """
    CONFIG_FIELDS = ('name', 'boxing_code', 'watch_dir', 'dest_dir', 'max_cycles')

    station = models.ForeignKey(CycleStation, on_delete=models.CASCADE, related_name='channels')
    name = models.CharField(max_length=255, blank=True, default='')
    # Pusty kod = kanał bez automatycznego zerowania z Eclipse (np. przeniesiony ze starej konfiguracji)
    boxing_code = models.CharField(max_length=64, blank=True, default='')
    watch_dir = models.CharField(max_length=500)
    dest_dir = models.CharField(max_length=500)
    max_cycles = models.PositiveIntegerField(default=69)

    # Stan raportowany przez mastera
    counter = models.PositiveIntegerField(default=0)
    last_cycle_at = models.DateTimeField(null=True, blank=True)

    # Ostatni NumQA z [Eclipse].[qav].[Batch] dla boxing_code (serwis BGS-ur-eclipse-sync)
    last_num_qa = models.BigIntegerField(null=True, blank=True)
    num_qa_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['station', 'id']
        constraints = [
            models.UniqueConstraint(fields=['boxing_code'], condition=~models.Q(boxing_code=''),
                                    name='ur_cycle_channel_unique_boxing_code'),
        ]

    def __str__(self):
        return f'{self.station.name} – {self.label}'

    @property
    def label(self) -> str:
        return self.name or self.boxing_code or f'Kanał {self.pk}'

    @property
    def limit_reached(self) -> bool:
        return self.counter >= self.max_cycles


class CycleStationCommand(BaseModel):
    class Type(models.TextChoices):
        RESET_COUNTER = 'RESET_COUNTER', 'Zerowanie licznika'
        RESTART_MASTER = 'RESTART_MASTER', 'Reset mistrza'

    station = models.ForeignKey(CycleStation, on_delete=models.CASCADE, related_name='commands')
    type = models.CharField(max_length=20, choices=Type)
    # Zerowanie jednego kanału; puste = wszystkie kanały stanowiska
    channel = models.ForeignKey(CycleChannel, on_delete=models.CASCADE, null=True, blank=True,
                                related_name='commands')
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['station', 'acked_at'])]

    def __str__(self):
        return f"{self.station.name} – {self.get_type_display()}"


class CycleStationEvent(BaseModel):
    class Kind(models.TextChoices):
        CYCLE = 'CYCLE', 'Cykl'
        LIMIT_REACHED = 'LIMIT_REACHED', 'Koniec produkcji – osiągnięty limit cykli'
        COMMAND_ISSUED = 'COMMAND_ISSUED', 'Wydano polecenie'
        COUNTER_RESET = 'COUNTER_RESET', 'Licznik wyzerowany'
        MASTER_RESTARTED = 'MASTER_RESTARTED', 'Restart mistrza'
        WORKER_RESTARTED = 'WORKER_RESTARTED', 'Restart workera'
        BACKEND_UNAVAILABLE = 'BACKEND_UNAVAILABLE', 'Backend był niedostępny'
        TESTER_CLOSED = 'TESTER_CLOSED', 'Zamknięto aplikację testującą'
        CONFIG_CHANGED = 'CONFIG_CHANGED', 'Zmiana konfiguracji'
        ERROR = 'ERROR', 'Błąd stanowiska'
        KEY_CONFLICT = 'KEY_CONFLICT', 'Klucz używany na dwóch komputerach'
        HOST_CHANGED = 'HOST_CHANGED', 'Zmiana komputera stanowiska'
        QA_BATCH_CHANGED = 'QA_BATCH_CHANGED', 'Nowa paczka QA w Eclipse'

    # Rodzaje, które master może zgłosić sam (reszta powstaje w backendzie)
    AGENT_KINDS = (Kind.MASTER_RESTARTED, Kind.WORKER_RESTARTED, Kind.BACKEND_UNAVAILABLE,
                   Kind.TESTER_CLOSED, Kind.ERROR)
    # Zagrożenia – widoczne w panelu, dopóki ktoś ich nie potwierdzi
    THREAT_KINDS = (Kind.KEY_CONFLICT, Kind.HOST_CHANGED, Kind.BACKEND_UNAVAILABLE,
                    Kind.WORKER_RESTARTED, Kind.ERROR)

    station = models.ForeignKey(CycleStation, on_delete=models.CASCADE, related_name='events')
    channel = models.ForeignKey(CycleChannel, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='events')
    kind = models.CharField(max_length=24, choices=Kind)
    message = models.CharField(max_length=500, blank=True, default='')
    counter = models.PositiveIntegerField(null=True, blank=True)
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='acknowledged_cycle_events')

    @property
    def is_threat(self) -> bool:
        return self.kind in self.THREAT_KINDS

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['station', '-created_at'])]

    def __str__(self):
        return f"{self.station.name} – {self.kind} {self.created_at}"

