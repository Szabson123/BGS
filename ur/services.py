from datetime import datetime, timedelta
from django.utils import timezone
from django.db import transaction

from .models import Breakdown, BreakdownMove, WorkshopParticipant, AdditionalEndingBreakdownInfo, Machine, WorkSchedulePreset, ScheduleBreak
from rest_framework.exceptions import ValidationError


def get_machine_break_status(machine: Machine, current_dt: datetime = None):
    if current_dt is None:
        current_dt = timezone.localtime()
    
    current_time_str = current_dt.strftime("%H:%M:%S")
    current_date = current_dt.date()
    
    preset = machine.schedules.filter(is_active=True).first()
    if not preset:
        return {
            "machine_id": machine.id,
            "machine_name": machine.name,
            "server_time": current_time_str,
            "server_datetime": current_dt.isoformat(),
            "current_schedule": None,
            "is_on_break": False,
            "current_break": None,
            "next_break": None,
            "all_breaks_today": [],
        }

    breaks = list(preset.breaks.all())
    
    active_break_data = None
    upcoming_breaks = []
    all_breaks_today = []

    # Check yesterday (-1), today (0), tomorrow (+1) for smooth cyclical and crossing midnight calculations
    for offset_days in [-1, 0, 1]:
        base_date = current_date + timedelta(days=offset_days)
        for b in breaks:
            naive_start = datetime.combine(base_date, b.start_time)
            if timezone.is_aware(current_dt):
                start_dt = timezone.make_aware(naive_start, timezone.get_current_timezone())
            else:
                start_dt = naive_start
            
            end_dt = start_dt + timedelta(minutes=b.duration_minutes)

            # Check if currently active
            if start_dt <= current_dt < end_dt:
                remaining_seconds = int((end_dt - current_dt).total_seconds())
                active_break_data = {
                    "id": b.id,
                    "name": b.name,
                    "start_time": b.start_time.strftime("%H:%M:%S"),
                    "end_time": end_dt.strftime("%H:%M:%S"),
                    "duration_minutes": b.duration_minutes,
                    "remaining_seconds": remaining_seconds,
                    "remaining_minutes": max(0, (remaining_seconds + 59) // 60),
                }

            # If starts in future
            if start_dt > current_dt:
                starts_in_seconds = int((start_dt - current_dt).total_seconds())
                upcoming_breaks.append({
                    "start_dt": start_dt,
                    "data": {
                        "id": b.id,
                        "name": b.name,
                        "start_time": b.start_time.strftime("%H:%M:%S"),
                        "end_time": end_dt.strftime("%H:%M:%S"),
                        "duration_minutes": b.duration_minutes,
                        "starts_in_seconds": starts_in_seconds,
                        "starts_in_minutes": max(0, (starts_in_seconds + 59) // 60),
                    }
                })

            if offset_days == 0:
                all_breaks_today.append({
                    "id": b.id,
                    "name": b.name,
                    "start_time": b.start_time.strftime("%H:%M:%S"),
                    "end_time": end_dt.strftime("%H:%M:%S"),
                    "duration_minutes": b.duration_minutes,
                    "order": b.order
                })

    upcoming_breaks.sort(key=lambda x: x["start_dt"])
    next_break_data = upcoming_breaks[0]["data"] if upcoming_breaks else None

    return {
        "machine_id": machine.id,
        "machine_name": machine.name,
        "server_time": current_time_str,
        "server_datetime": current_dt.isoformat(),
        "current_schedule": {
            "id": preset.id,
            "name": preset.name,
            "shift_duration_hours": preset.shift_duration_hours,
        },
        "is_on_break": active_break_data is not None,
        "current_break": active_break_data,
        "next_break": next_break_data,
        "all_breaks_today": all_breaks_today,
    }


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