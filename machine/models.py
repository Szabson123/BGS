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
                    'Breakdowns',
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

class WorkShopparticipant(BaseModel):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='workshopparticipant')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='workshopparticipant')


class CurrentWorkshop(BaseModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='currentworkshop')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='currentworkshop')


class Department(BaseModel):
    name = models.CharField(max_length=255)


class CurrentDepartment(BaseModel):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='currentdepartment')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='currentdepartment')


class Machine(BaseModel):
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    workshop = models.ForeignKey(Workshop, on_delete=models.CASCADE, null=True, blank=True, related_name='machines')
    name = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, null=True, blank=True)
    phase_id = models.CharField(max_length=24, null=True, blank=True)
    sigip_num = models.CharField(max_length=255, null=True, blank=True)


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


class Breakdown(BaseModel):
    class Priority(models.TextChoices):
        NONE = 'NONE', 'Brak'
        MID = 'MID', 'Średni'
        HIGH = 'HIGH', 'Wysoki'

    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='Breakdowns')
    priority = models.CharField(max_length=4, choices=Priority, default=Priority.NONE)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='Breakdowns')
    description = models.CharField(max_length=1024, null=True, blank=True)
    objects = BreakdownQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.machine.name} {self.created_at}"
    

class AdditionalEndingBreakdownInfo(BaseModel):
    breakdown = models.OneToOneField(Breakdown, on_delete=models.CASCADE, related_name='additional')
    closing_breakdown_type = models.ForeignKey(ClosingBreakdownTypes, on_delete=models.CASCADE)
    responsible_for_Breakdown = models.ForeignKey(ResponsibleForBreakdown, on_delete=models.CASCADE)
    

class BreakdownMove(BaseModel):
    class Status(models.TextChoices):
        REPORTED = 'RP', 'Zgłoszony'
        STARTED = 'ST', 'W Naprawie'
        ENDED = 'ED', 'Zakończony'

    breakdown = models.ForeignKey(Breakdown, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=2, choices=Status)
    description = models.CharField(max_length=1024, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['breakdown', '-created_at']),
        ]

    def __str__(self):
        return f"{self.breakdown.machine.name} - {self.status} {self.created_at}"
    
