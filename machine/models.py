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
    def with_full_history(self):
        return Machine.objects.select_related('department').prefetch_related(
            Prefetch(
                'breakdowns',
                BreakDown.objects.select_related('reporter').prefetch_related(
                    Prefetch(
                        'history',
                        BreakDownMove.objects.select_related('user')
                    )
                ).order_by('-created_at')
            )
        )
    
class BreakDownQuerySet(models.QuerySet):
    def with_last_status(self):
        statuses = BreakDownMove.objects.select_related('user').annotate(
            row_number=Window( # Deklaracja Wiaderka
                expression=RowNumber(), # Deklaracja że będziemy numerować rzędy w "Wiaderku"
                partition_by=F('break_down_id'), # Liczby będą niezależne od obiektu BreakDown czyli bedziemy restować nasze liczby co breakdown
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

class WorkShopParticipant(BaseModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='workshopparticipant')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='workshopparticipant')


class CurrentWorkshop(BaseModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='currentworkshop')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='currentworkshop')


class Department(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.SET_NULL, null=True, blank=True, related_name='departments')
    name = models.CharField(max_length=255)


class Machine(BaseModel):
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    name = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, null=True, blank=True)
    phase_id = models.CharField(max_length=24, null=True, blank=True)

    objects = MachineQuerySet.as_manager()

    def __str__(self):
        return self.name
    

class ClosingBreakdownTypes(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)


class ResponsibleForBreakdown(BaseModel):
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    

class MachineNotes(BaseModel):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='notes')
    description = models.CharField(max_length=1025)


class BreakDown(BaseModel):
    class Priority(models.TextChoices):
        NONE = 'NONE', 'None'
        MID = 'MID', 'Mid'
        HIGH = 'HIGH', 'High'

    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='breakdowns')
    priority = models.CharField(max_length=4, choices=Priority, default=Priority.NONE)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='breakdowns')
    description = models.CharField(max_length=1024, null=True, blank=True)
    objects = BreakDownQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.machine.name} {self.created_at}"
    

class AdditionalEndingBreakDownInfo(BaseModel):
    break_down = models.OneToOneField(BreakDown, on_delete=models.CASCADE)
    closing_break_down_type = models.ManyToManyField(ClosingBreakdownTypes)
    responsible_for_breakdown = models.ManyToManyField(ResponsibleForBreakdown)
    

class BreakDownMove(BaseModel):
    class Status(models.TextChoices):
        REPORTED = 'RP', 'Reported'
        STARTED = 'ST', 'Started'
        ENDED = 'ED', 'Ended'

    break_down = models.ForeignKey(BreakDown, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=2, choices=Status)
    description = models.CharField(max_length=1024, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['break_down', '-created_at']),
        ]

    def __str__(self):
        return f"{self.break_down.machine.name} - {self.status} {self.time}"
    
