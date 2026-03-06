from django.shortcuts import render
from django.db.models import Prefetch

from rest_framework import viewsets
from rest_framework.generics import ListAPIView

from .models import BreakDown, BreakDownMove
from .serializers import BreakDownListSerializer

class BreakDownListView(ListAPIView):
    serializer_class = BreakDownListSerializer
    
    def get_queryset(self):
        break_downs = (BreakDown.objects
                   .select_related('reporter', 'machine')
                   .prefetch_related('history')
        )
        
        return break_downs
        