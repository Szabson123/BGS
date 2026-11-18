from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    FamilyViewSet, OperationViewSet, PhaseViewSet, FactoryViewSet, DepartmentViewSet,
    DocumentViewSet, DocumentVersionFileView, PendingApprovalsView, ApprovalStepDecisionView, StepAttachmentFileView,
    OperatorDocumentsView, DocUserViewSet, ConversionCallbackView, RetryConversionView,
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
    path('versions/<int:version_id>/convert/', RetryConversionView.as_view(), name='doc-version-convert'),
    path('versions/<int:version_id>/conversion-callback/', ConversionCallbackView.as_view(),
         name='doc-conversion-callback'),
    path('approvals/', PendingApprovalsView.as_view(), name='doc-pending-approvals'),
    path('step-attachments/<int:attachment_id>/file/', StepAttachmentFileView.as_view(), name='doc-step-attachment-file'),
    path('approval-steps/<int:step_id>/approve/', ApprovalStepDecisionView.as_view(),
         {'decision': 'approve'}, name='doc-step-approve'),
    path('approval-steps/<int:step_id>/reject/', ApprovalStepDecisionView.as_view(),
         {'decision': 'reject'}, name='doc-step-reject'),
    path('', include(router.urls)),
]
