from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    card_code = models.CharField(max_length=255, blank=True, null=True, unique=True)
    number = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return self.username


class MaintenanceRole(models.Model):
    user = models.ManyToManyField(CustomUser, related_name='maintroles')
    name = models.CharField(max_length=255)


class MaintenancePermissions(models.Model):
    user = models.ManyToManyField(CustomUser, related_name='maintpermissions')
    name = models.CharField(max_length=255)