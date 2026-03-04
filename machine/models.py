from django.db import models
from user.models import CustomUser

class Workshop(models.Model):
    name = models.CharField(max_length=255, unique=True)


class Machine(models.Model):
    workshop = models.ForeignKey(Workshop, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    name = models.CharField(max_length=255)
    alias = models.CharField(max_length=255, null=True, blank=True)


class BreakDown(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name='breakdowns')
    date_added = models.DateTimeField(auto_now_add=True)