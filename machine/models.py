from django.db import models
from user.models import CustomUser

class Workshop(models.Model):
    name = models.CharField(max_length=255, unique=True)


class Machine(models.Model):
    workshop = models.ForeignKey(Workshop, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    name = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, null=True, blank=True)


class BreakDown(models.Model):
    class Priority(models.TextChoices):
        NONE = 'NONE', 'None'
        LOW = 'LOW', 'Low'
        MID = 'MID', 'Mid'
        HIGH = 'HIGH', 'High'

    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name='breakdowns')
    date_added = models.DateTimeField(auto_now_add=True)
    priority = models.CharField(max_length=4, choices=Priority, default=Priority.NONE)
    reporter = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='breakdowns')
    description = models.CharField(max_length=1024, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['-date_added']),
        ]


class BreakDownMove(models.Model):
    class Status(models.TextChoices):
        REPORTED = 'RP', 'Reported'
        STARTED = 'ST', 'Started'
        ENDED = 'ED', 'Ended'

    break_down = models.ForeignKey(BreakDown, on_delete=models.CASCADE, related_name='history')
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=2, choices=Status)
    time = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-time']
        indexes = [
            models.Index(fields=['time']),
            models.Index(fields=['status'])
        ]