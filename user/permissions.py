from rest_framework.permissions import BasePermission


QUALITY_GROUP = 'ur_quality'


def is_quality_user(user) -> bool:
    """Quality Worker – widzi raport awarii z departamentów wybranych w profilu."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=QUALITY_GROUP).exists()


UR_STAFF_GROUPS = ['ur_admin', 'ur_owner', 'ur_supervisor', 'ur_cordinator', 'ur_worker']
DEPARTMENT_SCOPED_GROUPS = ['ur_production', 'ur_production_supervisor', QUALITY_GROUP]


def uses_department_scope(user) -> bool:
    """Produkcja, mistrz produkcji i Quality (bez ról UR) widzą awarie z aktualnych departamentów, a nie z warsztatu UR."""
    if not user or not user.is_authenticated or user.is_superuser:
        return False
    groups = set(user.groups.values_list('name', flat=True))
    return bool(groups & set(DEPARTMENT_SCOPED_GROUPS)) and not (groups & set(UR_STAFF_GROUPS))


def is_production_admin(user) -> bool:
    """Administrator zarządza wszystkimi zespołami produkcji; supervisor tylko swoimi."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name='ur_admin').exists()


class IsURAdminOrOwner(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
            
        if request.user.is_superuser:
            return True

        return request.user.groups.filter(name__in=['ur_admin', 'ur_owner', 'ur_supervisor', 'ur_production_supervisor']).exists()


class IsURSupervisorOrAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        return request.user.groups.filter(
            name__in=['ur_admin', 'ur_owner', 'ur_supervisor', 'ur_production_supervisor']
        ).exists()


class IsProductionSupervisorOrAdmin(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if request.user.is_superuser:
            return True

        return request.user.groups.filter(
            name__in=['ur_admin', 'ur_production_supervisor']
        ).exists()