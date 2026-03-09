from django.urls import path
from .views import *


urlpatterns = [
    path('all-break-downs/', BreakDownListView.as_view(), name='break-downs'),
    path('create/break-down/', BreakDownCreateView.as_view(), name='craete-break-down'),
    path('move/break-down', BreakDownMakeMove.as_view(), name='move-break-down')
]