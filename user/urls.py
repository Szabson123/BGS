from django.urls import path
from .views import LoginAPIView, LogoutAPIView, MeAPIView, CsrfAPIView


urlpatterns = [
    path("auth/login/", LoginAPIView.as_view()),
    path("auth/logout/", LogoutAPIView.as_view()),
    path("auth/me/", MeAPIView.as_view()),
    path("auth/csrf/", CsrfAPIView.as_view()),
]