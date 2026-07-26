from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = 'Tworzy lub aktualizuje podstawowe role (grupy) dla modułu BGS::UR'

    def handle(self, *args, **options):
        ROLES = [
            'ur_admin',
            'ur_owner',
            'ur_cordinator',
            'ur_worker',
            'ur_production',
            'ur_supervisor',
        ]

        self.stdout.write(self.style.NOTICE('Rozpoczynam tworzenie ról dla BGS::UR...'))

        created_count = 0
        existing_count = 0

        for role_name in ROLES:
            group, created = Group.objects.get_or_create(name=role_name)
            
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  [+] Utworzono rolę: {role_name}'))
            else:
                existing_count += 1
                self.stdout.write(self.style.WARNING(f'  [-] Rola już istnieje: {role_name}'))

        self.stdout.write(
            self.style.SUCCESS(
                f'\nZakończono! Utworzono nowych: {created_count}, już istniejących: {existing_count}.'
            )
        )