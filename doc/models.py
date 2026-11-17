import os

from django.conf import settings
from django.db import models


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --- Słowniki (zarządza administrator DOC) ---------------------------------
# Hierarchia: Rodzina -> Operacja -> Faza. Fabryka i Departament są niezależne.

class Family(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Operation(BaseModel):
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name='operations')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['family', 'name'], name='doc_operation_unique_in_family'),
        ]

    def __str__(self):
        return f"{self.family} / {self.name}"


class Phase(BaseModel):
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name='phases')
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['operation', 'name'], name='doc_phase_unique_in_operation'),
        ]

    def __str__(self):
        return f"{self.operation} / {self.name}"


class Factory(BaseModel):
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'factories'

    def __str__(self):
        return self.name


class Department(BaseModel):
    """Departament modułu DOC – niezależny od departamentów UR."""
    name = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# --- Dokumenty i wersje ----------------------------------------------------

class Document(BaseModel):
    """Dokument przypięty do fazy (a przez nią do operacji i rodziny). Kilka dokumentów może mieć tę samą fazę."""
    title = models.CharField(max_length=255)
    phase = models.ForeignKey(Phase, on_delete=models.PROTECT, related_name='documents')
    # Fabryka i departament – informacja dla dodającego, operator ich nie widzi
    factory = models.ForeignKey(Factory, on_delete=models.PROTECT, related_name='documents')
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='documents')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='doc_documents')

    class Meta:
        ordering = ['-created_at']

    @property
    def number(self) -> str:
        return f"INS-{self.pk:04d}"

    def __str__(self):
        return f"{self.number} {self.title}"


def document_version_upload_to(instance, filename):
    return os.path.join('doc', 'documents', str(instance.document_id), f"v{instance.version_number}", filename)


class DocumentVersion(BaseModel):
    """
    Kolejna wersja pliku dokumentu. Wersji się nie nadpisuje.
    Operator widzi tylko najnowszą zatwierdzoną wersję dokumentu.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Oczekuje na akceptację'
        APPROVED = 'APPROVED', 'Zatwierdzona'
        REJECTED = 'REJECTED', 'Odrzucona'

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()
    file = models.FileField(upload_to=document_version_upload_to)
    original_filename = models.CharField(max_length=255)
    change_description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='doc_uploaded_versions')
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='doc_decided_versions'
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    class Meta:
        ordering = ['-version_number']
        constraints = [
            models.UniqueConstraint(fields=['document', 'version_number'], name='doc_version_unique_number'),
        ]

    def __str__(self):
        return f"{self.document.number} v{self.version_number} ({self.status})"

    def active_steps(self):
        """Kroki, które teraz czekają na decyzję: oczekujące z najniższym numerem kolejności.
        Kroki z tym samym numerem to kroki równoległe (na razie interfejs tworzy tylko kolejne)."""
        if self.status != self.Status.PENDING:
            return []
        pending = [step for step in self.approval_steps.all() if step.status == ApprovalStep.Status.PENDING]
        if not pending:
            return []
        first_order = min(step.order for step in pending)
        return [step for step in pending if step.order == first_order]


class ApprovalStep(BaseModel):
    """Krok ścieżki akceptacji wersji – decyduje dowolna osoba z danej sekcji (grupa doc_quality / doc_bhp / ...)."""
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Oczekuje'
        APPROVED = 'APPROVED', 'Zatwierdzony'
        REJECTED = 'REJECTED', 'Odrzucony'

    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name='approval_steps')
    order = models.PositiveIntegerField()
    section = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=Status, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='doc_approval_decisions'
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['version', 'section'], name='doc_step_unique_section_in_version'),
        ]

    def __str__(self):
        return f"{self.version} · {self.order}. {self.section} ({self.status})"


def step_attachment_upload_to(instance, filename):
    return os.path.join('doc', 'approval-notes', str(instance.step_id), filename)


class ApprovalStepAttachment(BaseModel):
    """Plik do notatki przy decyzji (np. zrzut ekranu dołączony do odrzucenia)."""
    step = models.ForeignKey(ApprovalStep, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=step_attachment_upload_to)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='doc_step_attachments')

    class Meta:
        ordering = ['id']

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith('image/')

    def __str__(self):
        return f"{self.step} · {self.original_filename}"
