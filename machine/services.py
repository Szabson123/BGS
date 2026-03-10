from django.db import transaction

from .models import BreakDown, BreakDownMove


def create_breakdown_with_initial_move(user, breakdown_data):
    with transaction.atomic():
        breakdown = BreakDown.objects.create(reporter=user, **breakdown_data)
        BreakDownMove.objects.create(
            break_down=breakdown,
            user=user,
            status='RP'
        )
    return breakdown

def move_breakdown(user, status_val, break_down, description):
    with transaction.atomic():
        obj = BreakDownMove.objects.create(
            break_down=break_down,
            status=status_val,
            user=user,
            description=description
        )

        return obj