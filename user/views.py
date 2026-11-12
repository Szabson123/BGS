from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from .permissions import IsURAdminOrOwner, IsProductionSupervisorOrAdmin
from .serializers import (
    CreateUserByAdminSerializer,
    CreateProductionUserSerializer,
    FirstPasswordSetSerializer,
    ResetUserPasswordSerializer,
    MainPageSerializer,
)
from .services import build_auth_user_payload

from rest_framework.authentication import BasicAuthentication, SessionAuthentication

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

        return Response(build_auth_user_payload(user))


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({"detail": "Wylogowano"})


@method_decorator(never_cache, name='dispatch')
class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user.refresh_from_db()

        response = Response(build_auth_user_payload(user))
        response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response


class MainPageAPIView(APIView):
    """Użytkownik sam wybiera moduł startowy spośród modułów, do których ma dostęp."""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = MainPageSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        request.user.main_page = serializer.validated_data['main_page']
        request.user.save(update_fields=['main_page'])

        return Response(build_auth_user_payload(request.user))


class CsrfAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class AdminCreateUserView(APIView):
    permission_classes = [IsURAdminOrOwner]
    authentication_classes = [BasicAuthentication, SessionAuthentication]

    def post(self, request):
        serializer = CreateUserByAdminSerializer(
            data=request.data, 
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CreateProductionUserView(APIView):
    permission_classes = [IsProductionSupervisorOrAdmin]
    authentication_classes = [BasicAuthentication, SessionAuthentication]

    def post(self, request):
        serializer = CreateProductionUserSerializer(
            data=request.data,
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class FirstPasswordSetView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = FirstPasswordSetSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"message": "Hasło zostało ustawione. Konto jest aktywne, możesz się zalogować."}, 
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ResetUserPasswordView(APIView):
    permission_classes = [IsURAdminOrOwner]

    def post(self, request):
        serializer = ResetUserPasswordSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {
                    "message": f"Hasło dla użytkownika {user.username} zostało zresetowane.",
                    "username": user.username,
                    "is_in_change_password": user.is_in_change_password
                },
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)