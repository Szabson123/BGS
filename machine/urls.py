from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()

router.register(r'machines/(?P<workshop_id>\d+)', MachineViewSet, basename='machines')
router.register(r'workshops', WorkshopViewset, basename='workshops')
router.register(r'department', DepartmentViewSet, basename='department')
router.register(r'closing-breakdown-types/(?P<workshop_id>\d+)', ClosingBreakdownTypesViewset, basename='closing-breakdown-types')
router.register(r'responsible-for-breakdown/(?P<workshop_id>\d+)', ResponsibleForBreakdownViewset, basename='responsible-for-breakdown')
router.register(r'participants/(?P<workshop_id>\d+)', WorkshopParticipantViewset, basename='participants')
router.register(r'notes/(?P<machine_id>\d+)', MachineNotesViewSet, basename='notes')


urlpatterns = [
    path('', include(router.urls)),
    path('create/breakdown/', BreakdownCreateView.as_view(), name='craete-break-down'),
    path('move/breakdown/', BreakdownMakeMove.as_view(), name='move-break-down'),
    path('end/breakdown/', BreakdownMakeEndedMove.as_view(), name='end-break-down'),

    path('machines-to-current-workshop/', MachinesInCurrentWorkshop.as_view(), name='machines-to-current-workshop'),
    path('breakdown-to-machine/<int:machine_id>/', BreakdownListToMachine.as_view(), name='breakdown-to-machine'),

    path('all-breakdowns-to-report/', BreakdownListViewToReport.as_view(), name='break-downs'),
    path('history-move/', ListOfBreakdownsMoves.as_view(), name='breakdown-to-machine'),
    path('all-breakdowns/', BreakdownListView.as_view(), name='break-downs'),
    
    path('breakdown/raport/options/', BreakdownOptionsView.as_view(), name='breakdown-raport-options'),
    path('breakdown/moves/options/', BreakdownMoveOptionView.as_view(), name='breakdown-moves-options'),
    path('breakdown/create/options/', BreakdownCreateMachineHelper.as_view(), name='move-break-down'),
    path('breakdown/end-modal/options/', EndBreakdownOptionsView.as_view(), name='move-break-down'),

    path('ur/profile/dashboard/', URProfilePanel.as_view(), name='ur-profile-dashoboard'),
    path('change-current-workshop/', ChangingCurrentWorkshop.as_view(), name='change-current-workshop'),
]