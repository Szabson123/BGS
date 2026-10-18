from rest_framework.permissions import BasePermission


QUALITY_GROUP = 'ur_quality'


def is_quality_user(user) -> bool:
    """Quality Worker – widzi raport awarii z departamentów wybranych w profilu."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=QUALITY_GROUP).exists()


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