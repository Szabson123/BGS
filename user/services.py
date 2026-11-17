from django.contrib.auth import get_user_model

from .modules import get_user_modules


def generate_unique_username(first_name: str, last_name: str) -> str:
    """Login z 2 pierwszych liter imienia i 3 ostatnich nazwiska (jak przy kontach UR), z licznikiem przy kolizji."""
    User = get_user_model()
    fn = first_name.strip().lower()
    ln = last_name.strip().lower()
    base_username = f"{fn[:2]}{ln[-3:]}"
    username = base_username
    counter = 2
    while User.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1
    return username


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
