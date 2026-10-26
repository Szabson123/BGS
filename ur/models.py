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

