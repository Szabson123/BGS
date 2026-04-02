from django.shortcuts import render
from rest_framework import viewsets, status

from rest_framework.response import Response

from .models import OperatorUser
from .serializers import OperatorUserSerializer

class OperatorUserViewSet(viewsets.ModelViewSet):
    serializer_class = OperatorUserSerializer
    queryset = OperatorUser.objects.all()


