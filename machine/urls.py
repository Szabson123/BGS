from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()

router.register(r'machines/(?P<workshop_id>\d+)', MachineViewSet, basename='machines')
router.register(r'workshops', WorkshopViewset, basename='workshops')
router.register(r'department', DepartmentViewSet, basename='deparment')
router.register(r'closing-breakdown-types/(?P<workshop_id>\d+)', ClosingBreakDownTypesViewset, basename='closing-breakdown-types')
router.register(r'responsible-for-breakdown/(?P<workshop_id>\d+)', ResponsibleForBreakdownViewset, basename='responsible-for-breakdown')


urlpatterns = [
    path('', include(router.urls)),
    path('all-break-downs/', BreakDownListView.as_view(), name='break-downs'),
    path('create/break-down/', BreakDownCreateView.as_view(), name='craete-break-down'),
    path('move/break-down/', BreakDownMakeMove.as_view(), name='move-break-down'),
    path('end/break-down/', BreakDownMakeEndedMove.as_view(), name='end-break-down'),
    path('create/break-down/machine-list/helper/', BreakDownCreateMachineHelper.as_view(), name='move-break-down'),
    path('move/break-down/types/helper/', ClosingBreakDownTypesHelper.as_view(), name='types-helper'),
    path('move/break-down/responsible/helper/', ResponsibleForBreakdownHelper.as_view(), name='responsible-helper'),
    path('all-break-downs-to-report/', BreakDownListViewToReport.as_view(), name='break-downs'),
    path('machines-to-current-workshop/', MachinesInCurrentWorkshop.as_view(), name='machines-to-current-workshop'),
    path('breakdown-to-machine/<int:machine_id>/', BreakdownListToMachine.as_view(), name='breakdown-to-machine'),
    path('history-move/', ListOfBreakDownsMoves.as_view(), name='breakdown-to-machine'),
]