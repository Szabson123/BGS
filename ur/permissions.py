from rest_framework import permissions

from user.modules import get_user_modules
from user.permissions import PROCESS_ENGINEER_GROUP

CYCLE_GUARD_MANAGER_GROUPS = ['ur_admin', PROCESS_ENGINEER_GROUP]


def can_manage_cycle_guard(user) -> bool:
    """Konfiguracja stanowisk, zerowanie licznika i reset mistrza."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name__in=CYCLE_GUARD_MANAGER_GROUPS).exists()


class CycleGuardPermission(permissions.BasePermission):
    """Podgląd: każdy z dostępem do BGS::UR. Zmiany i polecenia: admin UR / inżynier procesu."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return 'UR' in get_user_modules(user)
        return can_manage_cycle_guard(user)


class HasCycleStationKey(permissions.BasePermission):
    """Endpointy agenta (aplikacja master) – stanowisko rozpoznane po kluczu X-Api-Key."""

    def has_permission(self, request, view):
        from .models import CycleStation
        return isinstance(request.auth, CycleStation)


class IsURAdminOrOwnerOrReadOnlyParticipant(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser or request.user.groups.filter(name__in=['ur_admin', 'ur_owner']).exists():
            return True

        return request.method in permissions.SAFE_METHODS

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser or request.user.groups.filter(name__in=['ur_admin', 'ur_owner']).exists():
            return True

        return request.method in permissions.SAFE_METHODS