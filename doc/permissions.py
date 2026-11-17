from rest_framework.permissions import BasePermission, SAFE_METHODS

from user.modules import get_user_modules
from .roles import is_doc_admin, can_add_documents


class HasDocAccess(BasePermission):
    """Dowolna rola/sekcja DOC (lub superuser)."""
    def has_permission(self, request, view):
        return 'DOC' in get_user_modules(request.user)


class IsDocAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_doc_admin(request.user)


class IsDocAdminOrReadOnly(BasePermission):
    """Słowniki: odczyt dla każdego z dostępem do DOC, zmiany tylko administrator."""
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return 'DOC' in get_user_modules(request.user)
        return is_doc_admin(request.user)


class CanAddDocuments(BasePermission):
    def has_permission(self, request, view):
        return can_add_documents(request.user)
