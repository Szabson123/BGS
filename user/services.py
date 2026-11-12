from .modules import get_user_modules


def build_auth_user_payload(user) -> dict:
    """Dane zalogowanego użytkownika zwracane przez login i /me."""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "main_page": user.main_page,
        "groups": list(user.groups.values_list('name', flat=True)),
        "modules": get_user_modules(user),
        "is_superuser": user.is_superuser,
    }
