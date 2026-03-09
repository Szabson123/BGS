from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()

router.register(r'machines', MachineViewSet, basename='machines')

urlpatterns = [
    path('', include(router.urls)),
    path('all-break-downs/', BreakDownListView.as_view(), name='break-downs'),
    path('create/break-down/', BreakDownCreateView.as_view(), name='craete-break-down'),
    path('move/break-down/', BreakDownMakeMove.as_view(), name='move-break-down')
]