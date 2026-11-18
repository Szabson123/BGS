"""
Role i sekcje modułu BGS::DOC (grupy Django z prefiksem doc_, więc dają też dostęp do modułu).

- Role (rangi) decydują, co użytkownik może robić w DOC.
- Sekcje to grupy osób akceptujących. Sekcje z managerem (Quality, Production, Engineering) działają
  dwustopniowo: krok trafia najpierw do managera sekcji, a ten wskazuje osobę, która go rozpatrzy
  (manager może też zdecydować sam). W sekcjach bez managera (BHP) decyduje dowolna osoba z sekcji.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

ROLE_ADMIN = 'doc_admin'
ROLE_AUTHOR = 'doc_author'
ROLE_OPERATOR = 'doc_operator'

DOC_ROLES = {
    ROLE_ADMIN: 'Administrator',
    ROLE_AUTHOR: 'Dodający',
    ROLE_OPERATOR: 'Operator',
}

DOC_SECTIONS = {
    'doc_quality': 'Quality',
    'doc_bhp': 'BHP',
    'doc_production': 'Production',
    'doc_engineering': 'Engineering',
}

# Sekcja -> grupa jej managerów (sekcje spoza tej mapy nie mają etapu przypisania)
SECTION_MANAGERS = {
    'doc_quality': 'doc_quality_manager',
    'doc_production': 'doc_production_manager',
    'doc_engineering': 'doc_engineering_manager',
}

DOC_MANAGERS = {
    'doc_quality_manager': 'Quality Manager',
    'doc_production_manager': 'Production Manager',
    'doc_engineering_manager': 'Engineering Manager',
}

ALL_DOC_GROUPS = {**DOC_ROLES, **DOC_SECTIONS, **DOC_MANAGERS}


def user_doc_groups(user) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.filter(name__startswith='doc_').values_list('name', flat=True))


def is_managed(section: str) -> bool:
    return section in SECTION_MANAGERS


def managed_sections(user) -> set[str]:
    """Sekcje, w których użytkownik jest managerem."""
    groups = user_doc_groups(user)
    return {section for section, manager in SECTION_MANAGERS.items() if manager in groups}


def member_sections(user) -> set[str]:
    """Sekcje, do których użytkownik należy jako zwykły członek."""
    return user_doc_groups(user) & set(DOC_SECTIONS)


def user_sections(user) -> set[str]:
    """Sekcje, z którymi użytkownik jest związany (członek albo manager) – np. wgląd w niezatwierdzone wersje."""
    return member_sections(user) | managed_sections(user)


def section_people(section: str):
    """Aktywne osoby, którym manager może przypisać krok sekcji: jej członkowie i managerowie."""
    names = [section] + ([SECTION_MANAGERS[section]] if is_managed(section) else [])
    return get_user_model().objects.filter(groups__name__in=names, is_active=True).distinct()


def section_managers(section: str):
    if not is_managed(section):
        return get_user_model().objects.none()
    return get_user_model().objects.filter(groups__name=SECTION_MANAGERS[section], is_active=True).distinct()


def is_doc_admin(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or ROLE_ADMIN in user_doc_groups(user)))


def can_add_documents(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or bool(user_doc_groups(user) & {ROLE_ADMIN, ROLE_AUTHOR})


def set_user_doc_groups(user, group_names: set[str]) -> None:
    """Ustawia grupy DOC użytkownika, nie ruszając grup innych modułów (ur_* itd.)."""
    unknown = group_names - set(ALL_DOC_GROUPS)
    if unknown:
        raise ValueError(f"Nieznane grupy DOC: {', '.join(sorted(unknown))}")

    current = set(user.groups.filter(name__startswith='doc_'))
    wanted = {Group.objects.get_or_create(name=name)[0] for name in group_names}
    user.groups.remove(*(current - wanted))
    user.groups.add(*(wanted - current))
