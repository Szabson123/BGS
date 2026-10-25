from datetime import datetime, timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from .models import (Breakdown, BreakdownMove, WorkshopParticipant, AdditionalEndingBreakdownInfo, Machine, WorkSchedulePreset,
                     ScheduleBreak, ProductionTeam, CancelledBreakdownSnapshot)
from rest_framework.exceptions import ValidationError
from user.models import CustomUser
from user.permissions import is_production_admin


NON_PRODUCTION_WORKER_GROUPS = ['ur_admin', 'ur_owner', 'ur_production_supervisor', 'ur_supervisor', 'ur_worker', 'ur_cordinator']


def production_workers_queryset():
    return (
        CustomUser.objects.filter(groups__name='ur_production')
        .exclude(groups__name__in=NON_PRODUCTION_WORKER_GROUPS)
        .exclude(is_superuser=True)
        .distinct()
    )


def production_workers_visible_to(user):
    """Admin widzi wszystkich; supervisor pracowników ze swoich zespołów oraz nieprzypisanych do żadnego zespołu."""
    qs = production_workers_queryset()
    if is_production_admin(user):
        return qs
    return qs.filter(
        Q(production_teams__supervisors=user) | Q(production_teams__isnull=True)
    ).distinct()


def can_manage_reported_breakdown(user, breakdown) -> bool:
    """Anulować zgłoszenie i dopisywać do opisu może zgłaszający, mistrz produkcji, koordynator lub administrator."""
    if not user or not user.is_authenticated:
        return False
    if breakdown.reporter_id == user.id or is_production_admin(user):
        return True
    return user.groups.filter(name__in=['ur_production_supervisor', 'ur_cordinator']).exists()


@transaction.atomic
def cancel_breakdown(breakdown_id, user):
    """Usuwa jeszcze nierozpoczętą awarię i zapisuje jej snapshot."""
    breakdown = Breakdown.objects.select_for_update().select_related('machine').get(pk=breakdown_id)

    if breakdown.history.exclude(status=BreakdownMove.Status.REPORTED).exists():
        raise ValidationError("Nie można anulować awarii, której naprawa została już rozpoczęta.")

    history = [
        {
            "status": move.status,
            "user_id": move.user_id,
            "description": move.description,
            "created_at": move.created_at.isoformat(),
        }
        for move in breakdown.history.order_by('created_at')
    ]
    snapshot = CancelledBreakdownSnapshot.objects.create(
        original_breakdown_id=breakdown.id,
        machine=breakdown.machine,
        machine_name=breakdown.machine.name,
        reporter=breakdown.reporter,
        priority=breakdown.priority,
        description=breakdown.description,
        reported_at=breakdown.created_at,
        cancelled_by=user,
        history=history,
    )
    breakdown.delete()
    return snapshot


@transaction.atomic
def append_breakdown_description(breakdown_id, text):
    """Dopisuje tekst do opisu usterki w nowej linii – bez możliwości edycji wcześniejszej treści."""
    breakdown = Breakdown.objects.select_for_update().get(pk=breakdown_id)
    addition = (text or '').strip()

    if not addition:
        raise ValidationError("Wpisz treść do dopisania.")
    if breakdown.history.filter(status=BreakdownMove.Status.ENDED).exists():
        raise ValidationError("Nie można dopisywać do zakończonej awarii.")

    current = (breakdown.description or '').rstrip()
    new_description = f"{current}\n{addition}" if current else addition
    if len(new_description) > Breakdown.DESCRIPTION_MAX_LENGTH:
        raise ValidationError(f"Opis usterki może mieć maksymalnie {Breakdown.DESCRIPTION_MAX_LENGTH} znaków.")

    breakdown.description = new_description
    breakdown.save(update_fields=['description', 'updated_at'])
    return breakdown


@transaction.atomic
def transfer_production_teams(from_user, to_user, teams=None):
    """Przekazuje zespoły (wszystkie albo wskazane) od jednego supervisora drugiemu."""
    qs = from_user.supervised_production_teams.all()
    if teams is not None:
        qs = qs.filter(id__in=[team.id for team in teams])

    moved = list(qs)
    for team in moved:
        team.supervisors.remove(from_user)
        team.supervisors.add(to_user)
    return moved


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