from django.db import transaction

from .models import Breakdown, BreakdownMove, WorkshopParticipant, AdditionalEndingBreakdownInfo
from rest_framework.exceptions import ValidationError


def create_breakdown_with_initial_move(user, Breakdown_data):
    with transaction.atomic():
        breakdown = Breakdown.objects.create(reporter=user, **Breakdown_data)
        BreakdownMove.objects.create(
            breakdown=breakdown,
            user=user,
            status=BreakdownMove.Status.REPORTED
        )
    return Breakdown


class MoveBreakdownService():
    def __init__(self, user, status_val, breakdown, description):
        self.status_val = status_val
        self.breakdown = breakdown
        self.description = description
        self.user = user
    
    def execute(self):
        self._move_Breakdown()

    def _move_Breakdown(self):
        self.check_is_user_participant()
        self.check_is_not_ended_Breakdown()
        self.make_move()

    def make_move(self):
        with transaction.atomic():
            obj = BreakdownMove.objects.create(
                breakdown=self.breakdown,
                status=self.status_val,
                user=self.user,
                description=self.description
            )

    def check_is_user_participant(self):
        try:
            participant = WorkshopParticipant.objects.get(
                user = self.user,
                workshop = self.breakdown.machine.workshop
            )
        except:
            raise ValidationError('You are not participant in this workshop you cant move Breakdowns')
    
    def check_is_not_ended_Breakdown(self):
        obj = BreakdownMove.objects.filter(
            breakdown = self.breakdown,
            status = BreakdownMove.Status.ENDED
        ).exists()

        if obj:
            raise ValidationError('This Breakdown is ended')


class EndBreakdownService():
    def __init__(self, user, breakdown, description, closing_breakdown_type, responsible_for_breakdown):
        self.user = user
        self.breakdown = breakdown
        self.description = description
        self.closing_types = closing_breakdown_type
        self.responsible_people = responsible_for_breakdown
    
    @transaction.atomic
    def execute(self):
        self.check_is_user_participant()
        self.check_is_not_ended()

        move = BreakdownMove.objects.create(
            breakdown=self.breakdown,
            user=self.user,
            status=BreakdownMove.Status.ENDED,
            description=self.description
        )
        
        ending_info = AdditionalEndingBreakdownInfo.objects.create(
                breakdown=self.breakdown,
                closing_breakdown_type=self.closing_types,
                responsible_for_breakdown=self.responsible_people
            )
        
        ending_info.save()
    

    def check_is_user_participant(self):
        try:
            participant = WorkshopParticipant.objects.get(
                user = self.user,
                workshop = self.breakdown.machine.workshop
            )

        except:
            raise ValidationError('You are not participant in this workshop you cant move Breakdowns')
    

    def check_is_not_ended(self):
        if BreakdownMove.objects.filter(breakdown=self.breakdown, status=BreakdownMove.Status.ENDED).exists():
            raise ValidationError("Ta awaria została już zakończona.")
        
        if not BreakdownMove.objects.filter(breakdown=self.breakdown, status=BreakdownMove.Status.STARTED).exists():
            raise ValidationError("Ta awaria nie została rozpoczęta (brak statusu ST)")