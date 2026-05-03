from django.db import transaction

from .models import BreakDown, BreakDownMove, WorkShopParticipant, AdditionalEndingBreakDownInfo
from rest_framework.exceptions import ValidationError


def create_breakdown_with_initial_move(user, breakdown_data):
    with transaction.atomic():
        breakdown = BreakDown.objects.create(reporter=user, **breakdown_data)
        BreakDownMove.objects.create(
            break_down=breakdown,
            user=user,
            status=BreakDownMove.Status.REPORTED
        )
    return breakdown


class MoveBreakDownService():
    def __init__(self, user, status_val, break_down, description):
        self.status_val = status_val
        self.break_down = break_down
        self.description = description
        self.user = user
    
    def execute(self):
        self._move_breakdown()

    def _move_breakdown(self):
        self.check_is_user_participant()
        self.check_is_not_ended_breakdown()
        self.make_move()

    def make_move(self):
        with transaction.atomic():
            obj = BreakDownMove.objects.create(
                break_down=self.break_down,
                status=self.status_val,
                user=self.user,
                description=self.description
            )

    def check_is_user_participant(self):
        try:
            participant = WorkShopParticipant.objects.get(
                user = self.user,
                workshop = self.break_down.machine.department.workshop
            )
        except:
            raise ValidationError('You are not participant in this workshop you cant move breakdowns')
    
    def check_is_not_ended_breakdown(self):
        obj = BreakDownMove.objects.filter(
            break_down = self.break_down,
            status = BreakDownMove.Status.ENDED
        ).exists()

        print(obj)
        if obj:
            raise ValidationError('This breakdown is ended')



class EndBreakdownService():
    def __init__(self, user, break_down, description, closing_break_down_type, responsible_for_breakdown):
        self.user = user
        self.break_down = break_down
        self.description = description
        self.closing_types = closing_break_down_type
        self.responsible_people = responsible_for_breakdown
    
    @transaction.atomic
    def execute(self):
        self.check_is_user_participant()
        self.check_is_not_ended()

        move = BreakDownMove.objects.create(
            break_down=self.break_down,
            user=self.user,
            status=BreakDownMove.Status.ENDED,
            description=self.description
        )
        
        ending_info = AdditionalEndingBreakDownInfo.objects.create(
                break_down=self.break_down,
                closing_break_down_type=self.closing_types,
                responsible_for_breakdown=self.responsible_people
            )
        
        ending_info.save()
    

    def check_is_user_participant(self):
        try:
            participant = WorkShopParticipant.objects.get(
                user = self.user,
                workshop = self.break_down.machine.department.workshop
            )

        except:
            raise ValidationError('You are not participant in this workshop you cant move breakdowns')
    

    def check_is_not_ended(self):
        if BreakDownMove.objects.filter(break_down=self.break_down, status=BreakDownMove.Status.ENDED).exists():
            raise ValidationError("Ta awaria została już zakończona.")
        
        if not BreakDownMove.objects.filter(break_down=self.break_down, status=BreakDownMove.Status.STARTED).exists():
            raise ValidationError("Ta awaria nie została rozpoczęta (brak statusu ST)")