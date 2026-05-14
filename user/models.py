from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    class MainPageUser(models.TextChoices):
        Breakdown = 'Breakdown', 'Break Down'
        Breakdown_CRATE = 'Breakdown_CRATE', 'Break Down Create'
        ADMIN_PAGE = 'ADMIN_PAGE', 'Admin page'

    card_code = models.CharField(max_length=255, blank=True, null=True, unique=True)
    number = models.CharField(max_length=255, null=True, blank=True)
    main_page = models.CharField(choices=MainPageUser, max_length=255, default=MainPageUser.Breakdown_CRATE)

    def __str__(self):
        return self.username
    

class AppsRoleBGS(models.Model):
    user = models.ManyToManyField(CustomUser, related_name='approles')
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class MaintenanceRole(models.Model):
    user = models.ManyToManyField(CustomUser, related_name='maintroles')
    app = models.ForeignKey(AppsRoleBGS, on_delete=models.CASCADE, related_name='maintroles')
    name = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.name} -- {self.app.name}"

