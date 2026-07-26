from django.urls import path
from .views import LoginAPIView, LogoutAPIView, MeAPIView, CsrfAPIView, AdminCreateUserView, FirstPasswordSetView, ResetUserPasswordView


urlpatterns = [
    path("auth/login/", LoginAPIView.as_view()),
    path("auth/logout/", LogoutAPIView.as_view()),
    path("auth/me/", MeAPIView.as_view()),
    path("auth/csrf/", CsrfAPIView.as_view()),
    path("users/create-by-admin/", AdminCreateUserView.as_view(), name='create-ur-user'),
    path("users/first-password-set/", FirstPasswordSetView.as_view(), name='create-ur-user'),
    path('users/reset-password/', ResetUserPasswordView.as_view(), name='reset-user-password'),
]