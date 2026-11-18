"""
Powiadomienia modułu DOC – tworzone w tej samej transakcji co zdarzenie, które je wywołało.

- krok staje się aktywny: managerowie sekcji (TO_ASSIGN) albo, w sekcji bez managera, jej członkowie (TO_DECIDE)
- manager wskazał osobę: ta osoba (ASSIGNED)
- wersja zatwierdzona / odrzucona: dodający wersję (APPROVED / REJECTED)
Wykonawca zdarzenia nie dostaje powiadomienia o własnej akcji.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Notification, ApprovalStep
from .roles import DOC_SECTIONS, is_managed, section_managers

User = get_user_model()
Kind = Notification.Kind


def _label(version) -> str:
    document = version.document
    return f"{document.number} · {document.title} · v{version.version_number}"


def _create(recipients, kind, message, version, step=None, actor=None):
    Notification.objects.bulk_create([
        Notification(recipient=user, kind=kind, message=message[:500], document=version.document,
                     version=version, step=step, actor=actor)
        for user in recipients
        if not actor or user.pk != actor.pk
    ])


def notify_active_steps(version, actor=None) -> None:
    """Kroki, które właśnie stały się aktywne, trafiają do swoich sekcji."""
    for step in version.active_steps():
        section = DOC_SECTIONS.get(step.section, step.section)
        if is_managed(step.section):
            _create(section_managers(step.section), Kind.TO_ASSIGN,
                    f"{_label(version)} – wskaż osobę do akceptacji ({section}).", version, step, actor)
        else:
            members = User.objects.filter(groups__name=step.section, is_active=True).distinct()
            _create(members, Kind.TO_DECIDE,
                    f"{_label(version)} czeka na akceptację sekcji {section}.", version, step, actor)


def notify_assigned(step: ApprovalStep, actor) -> None:
    # Przypisanie załatwia „do przypisania” u wszystkich managerów sekcji
    mark_step_read(step, kinds=[Kind.TO_ASSIGN])
    section = DOC_SECTIONS.get(step.section, step.section)
    _create([step.assigned_to], Kind.ASSIGNED,
            f"{_label(step.version)} – przypisano Ci akceptację ({section}).", step.version, step, actor)


def notify_version_finished(version, actor, comment: str = '') -> None:
    if version.status == version.Status.APPROVED:
        _create([version.uploaded_by], Kind.APPROVED,
                f"{_label(version)} została zatwierdzona – operatorzy ją widzą.", version, actor=actor)
    else:
        reason = f" Powód: {comment}" if comment else ''
        _create([version.uploaded_by], Kind.REJECTED,
                f"{_label(version)} została odrzucona.{reason}", version, actor=actor)


def mark_step_read(step: ApprovalStep, kinds=None) -> None:
    """Powiadomienia o kroku, którego już nie trzeba ruszać (przypisany / rozpatrzony), znikają z nieprzeczytanych."""
    queryset = Notification.objects.filter(step=step, read_at__isnull=True)
    if kinds:
        queryset = queryset.filter(kind__in=kinds)
    queryset.update(read_at=timezone.now())
