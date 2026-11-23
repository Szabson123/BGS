"""
Konwersja arkuszy do PDF w mikroserwisie BGS-converter.

Fire & forget: po zapisaniu wersji plik idzie do konwertera w osobnym wątku (po commicie transakcji),
żądanie użytkownika na to nie czeka. Wynik wraca callbackiem (views.ConversionCallbackView).
"""
import logging
import os
import re
import threading
import unicodedata
import uuid
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import close_old_connections, transaction
from django.urls import reverse
from django.utils import timezone

from .models import DocumentVersion

logger = logging.getLogger(__name__)

CONVERTIBLE_EXTENSIONS = {'.xlsx', '.xlsm', '.xls', '.ods'}
Status = DocumentVersion.ConversionStatus


STAMP_PREFIX = 'BGSDOC'
_STAMP_FIELD_MAX = 40
# Numer i wersja z zeskanowanego kodu („BGSDOC|INS-0006|2|…”) albo wpisane ręcznie („INS-0006 v2”, „INS-6/2”)
_STAMP_RE = re.compile(r'INS-?0*(\d{1,9})\s*[|/ ]\s*v?(\d{1,6})', re.IGNORECASE)


def _ascii(text: str) -> str:
    """Bez polskich znaków i separatora – skanery w trybie klawiatury często psują znaki spoza ASCII."""
    text = text.replace('ł', 'l').replace('Ł', 'L')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    text = ' '.join(text.replace('|', '/').split())
    return text[:_STAMP_FIELD_MAX]


def stamp_payload(version: DocumentVersion) -> tuple[str, str]:
    """Treść kodu DataMatrix na 1. stronie PDF i podpis pod nim."""
    document = version.document
    phase = document.phase
    fields = [
        STAMP_PREFIX, document.number, str(version.version_number),
        _ascii(phase.operation.family.name), _ascii(phase.operation.name), _ascii(phase.name),
    ]
    return '|'.join(fields), f'{document.number} v{version.version_number}'


def parse_stamp(text: str) -> tuple[int, int] | None:
    """(id dokumentu, numer wersji) z zeskanowanego kodu albo None."""
    match = _STAMP_RE.search(text or '')
    return (int(match.group(1)), int(match.group(2))) if match else None


def needs_conversion(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in CONVERTIBLE_EXTENSIONS


def can_retry(version: DocumentVersion) -> bool:
    """Ponowić można po błędzie albo gdy zlecenie „w toku” wisi zbyt długo (np. restart konwertera)."""
    if not needs_conversion(version.original_filename):
        return False
    if version.conversion_status == Status.FAILED:
        return True
    if version.conversion_status == Status.PENDING and version.conversion_requested_at:
        stale_after = timedelta(minutes=settings.DOC_CONVERTER_STALE_MINUTES)
        return timezone.now() - version.conversion_requested_at > stale_after
    return False


def request_conversion(version: DocumentVersion) -> None:
    """Oznacza wersję jako „w toku” i po commicie wysyła plik do konwertera (bez czekania na wynik)."""
    version.conversion_status = Status.PENDING
    version.conversion_error = ''
    version.conversion_job_id = uuid.uuid4().hex
    version.conversion_requested_at = timezone.now()
    version.save(update_fields=[
        'conversion_status', 'conversion_error', 'conversion_job_id', 'conversion_requested_at', 'updated_at',
    ])
    job_id = version.conversion_job_id
    transaction.on_commit(lambda: start_background(send_to_converter, version.pk, job_id))


def start_background(target, *args) -> None:
    """Osobny punkt, żeby testy mogły wykonać wysyłkę synchronicznie."""
    def run():
        try:
            target(*args)
        finally:
            close_old_connections()  # wątek ma własne połączenie z bazą – zamykamy je po pracy

    threading.Thread(target=run, daemon=True, name=f'doc-convert-{args[0]}').start()


def send_to_converter(version_id: int, job_id: str) -> None:
    try:
        version = DocumentVersion.objects.select_related('document__phase__operation__family').get(pk=version_id)
        if version.conversion_job_id != job_id:
            return  # w międzyczasie ponowiono – wysyła już nowsze zlecenie
        if not settings.DOC_CONVERTER_URL:
            _mark_failed(version_id, job_id, 'Konwerter PDF nie jest skonfigurowany (DOC_CONVERTER_URL).')
            return

        callback_url = settings.DOC_CONVERTER_CALLBACK_BASE + reverse('doc-conversion-callback', args=[version_id])
        stamp_text, stamp_label = stamp_payload(version)
        with version.file.open('rb') as handle:
            response = httpx.post(
                f'{settings.DOC_CONVERTER_URL}/convert',
                headers={'X-Api-Key': settings.DOC_CONVERTER_API_KEY},
                data={'job_id': job_id, 'callback_url': callback_url,
                      'stamp_text': stamp_text, 'stamp_label': stamp_label},
                files={'file': (version.original_filename, handle)},
                timeout=httpx.Timeout(60, connect=5),
            )
        if response.status_code != 202:
            detail = _detail(response)
            _mark_failed(version_id, job_id, f'Konwerter odrzucił plik ({response.status_code}): {detail}')
    except httpx.HTTPError as exc:
        logger.warning('Konwerter niedostępny dla wersji %s: %s', version_id, exc)
        _mark_failed(version_id, job_id, f'Konwerter PDF jest niedostępny: {exc}')
    except Exception as exc:  # noqa: BLE001 – wątek w tle nie może zostawić wersji wiecznie „w toku”
        logger.exception('Błąd wysyłki do konwertera (wersja %s)', version_id)
        _mark_failed(version_id, job_id, f'Nie udało się wysłać pliku do konwertera: {exc}')


def _detail(response) -> str:
    try:
        return str(response.json().get('detail', ''))[:500]
    except ValueError:
        return response.text[:500]


def _mark_failed(version_id: int, job_id: str, error: str) -> None:
    # Tylko jeśli to nadal bieżące zlecenie – nie nadpisujemy wyniku nowszego
    DocumentVersion.objects.filter(pk=version_id, conversion_job_id=job_id, conversion_status=Status.PENDING).update(
        conversion_status=Status.FAILED, conversion_error=error[:2000], updated_at=timezone.now(),
    )
