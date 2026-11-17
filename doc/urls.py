from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    FamilyViewSet, OperationViewSet, PhaseViewSet, FactoryViewSet, DepartmentViewSet,
    DocumentViewSet, DocumentVersionDecisionView, DocumentVersionFileView,
    OperatorDocumentsView, DocUserViewSet,
)

router = DefaultRouter()
router.register(r'families', FamilyViewSet, basename='doc-families')
router.register(r'operations', OperationViewSet, basename='doc-operations')
router.register(r'phases', PhaseViewSet, basename='doc-phases')
router.register(r'factories', FactoryViewSet, basename='doc-factories')
router.register(r'departments', DepartmentViewSet, basename='doc-departments')
router.register(r'documents', DocumentViewSet, basename='doc-documents')
router.register(r'admin/users', DocUserViewSet, basename='doc-admin-users')

# Endpointy modułu BGS::DOC (/api/doc/...)
urlpatterns = [
    path('operator/documents/', OperatorDocumentsView.as_view(), name='doc-operator-documents'),
    path('versions/<int:version_id>/file/', DocumentVersionFileView.as_view(), name='doc-version-file'),
    path('versions/<int:version_id>/approve/', DocumentVersionDecisionView.as_view(),
         {'decision': 'approve'}, name='doc-version-approve'),
    path('versions/<int:version_id>/reject/', DocumentVersionDecisionView.as_view(),
         {'decision': 'reject'}, name='doc-version-reject'),
    path('', include(router.urls)),
]
