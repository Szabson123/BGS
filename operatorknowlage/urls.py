from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()

router.register(r'operators', OperatorUserViewSet, basename='machines')

urlpatterns = [
    path('', include(router.urls)),
]