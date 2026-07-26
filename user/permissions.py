from rest_framework.permissions import BasePermission

class IsURAdminOrOwner(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
            
        if request.user.is_superuser:
            return True

        return request.user.groups.filter(name__in=['ur_admin', 'ur_owner']).exists()