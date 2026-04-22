from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.db.models import prefetch_related_objects

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from rest_framework.permissions import IsAuthenticated


class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")

        if not username or not password:
            return Response(
                {"detail": "Username i password są wymagane"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, username=username, password=password)

        if user is None:
            return Response(
                {"detail": "Nieprawidłowe dane logowania"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        login(request, user)

        prefetch_related_objects([user], 'maintroles', 'maintpermissions')

        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "main_page": user.main_page,
        })
    

class LogoutAPIView(APIView):
    def post(self, request):
        logout(request)
        return Response({"detail": "Wylogowano"})
    

class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        prefetch_related_objects([user], 'maintroles', 'maintpermissions')

        app_list = list(user.approles.values_list('name', flat=True))
        roles_list = list(user.maintroles.values_list('name', flat=True))

        return Response({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,

            "avaible_apps": app_list,
            "roles_list": roles_list,
        })
    

class CsrfAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})