"""
Role i sekcje modułu BGS::DOC (grupy Django z prefiksem doc_, więc dają też dostęp do modułu).

- Role (rangi) decydują, co użytkownik może robić w DOC.
- Sekcje to grupy osób akceptujących – na razie tylko przypisanie, akceptacje dojdą później.
"""
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
}

ALL_DOC_GROUPS = {**DOC_ROLES, **DOC_SECTIONS}


def user_doc_groups(user) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    return set(user.groups.filter(name__startswith='doc_').values_list('name', flat=True))


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
