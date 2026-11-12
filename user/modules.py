"""
Rejestr modułów BGS (BGS::UR, BGS::DOC, ...).

Dostęp do modułu wynika z grup użytkownika: posiadanie dowolnej grupy
z prefiksem modułu (np. `doc_editor`) daje dostęp do tego modułu.
Superuser ma dostęp do wszystkich modułów.

Nowy moduł = nowy wpis w MODULES (+ odpowiadający wpis w rejestrze na froncie).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BGSModule:
    key: str
    label: str
    group_prefix: str


MODULES: tuple[BGSModule, ...] = (
    BGSModule(key='UR', label='BGS::UR', group_prefix='ur_'),
    BGSModule(key='DOC', label='BGS::DOC', group_prefix='doc_'),
)

MODULE_CHOICES = [(module.key, module.label) for module in MODULES]
DEFAULT_MODULE = MODULES[0].key


def get_user_modules(user) -> list[str]:
    """Klucze modułów, do których użytkownik ma dostęp (w kolejności rejestru)."""
    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return [module.key for module in MODULES]

    group_names = list(user.groups.values_list('name', flat=True))
    return [
        module.key
        for module in MODULES
        if any(name.startswith(module.group_prefix) for name in group_names)
    ]
