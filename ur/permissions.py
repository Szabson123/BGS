from rest_framework import permissions

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