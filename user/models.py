from django.contrib.auth.models import AbstractUser
from django.db import models

from .modules import MODULE_CHOICES, DEFAULT_MODULE


class CustomUser(AbstractUser):
    card_code = models.CharField(max_length=255, blank=True, null=True, unique=True)
    number = models.CharField(max_length=255, null=True, blank=True)
    # Moduł startowy (BGS::UR, BGS::DOC, ...), do którego użytkownik trafia po zalogowaniu
    main_page = models.CharField(choices=MODULE_CHOICES, max_length=255, default=DEFAULT_MODULE)
    is_active = models.BooleanField(default=False)
    is_in_change_password = models.BooleanField(default=True)

    def __str__(self):
        return self.username
