from django.urls import path
from .views import *



urlpatterns = [
    path('all-break-downs/', BreakDownListView.as_view(), name='break-downs')
]