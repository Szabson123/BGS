from django_filters import rest_framework as filters
from .models import Breakdown, BreakdownMove
from user.models import CustomUser
from django.db.models import Subquery, OuterRef
from user.models import CustomUser
from django.db import models


class BreakdownFilter(filters.FilterSet):
    date_range = filters.DateTimeFromToRangeFilter(field_name='created_at', label='Data zgłoszenia (od-do)')

    status = filters.ChoiceFilter(
        choices=BreakdownMove.Status.choices,
        method='filter_by_last_status',
        label='Zawiera status w historii'
    )

    technician = filters.ModelChoiceFilter(
        field_name='history__user',
        queryset=CustomUser.objects.all(),
        label='Serwisant biorący udział'
    )

    search = filters.CharFilter(method='filter_by_all_descriptions', label='Szukaj w opisach')
    close_type = filters.NumberFilter(field_name='additional__closing_breakdown_type__id')
    responsible = filters.NumberFilter(field_name='additional__responsible_for_Breakdown__id')

    class Meta:
        model = Breakdown
        fields = ['priority', 'reporter', 'machine', 'close_type', 'responsible']

    def filter_by_all_descriptions(self, queryset, name, value):
        return queryset.filter(
            models.Q(description__icontains=value) | 
            models.Q(history__description__icontains=value)
        ).distinct()
    
    def filter_by_last_status(self, queryset, name, value):
        latest_status_subquery = BreakdownMove.objects.filter(
            breakdown=OuterRef('pk')
        ).order_by('-created_at').values('status')[:1]

        return queryset.annotate(
            last_status_val=Subquery(latest_status_subquery)
        ).filter(last_status_val=value)
    

class BreakdownMoveFilter(filters.FilterSet):
    date = filters.DateTimeFromToRangeFilter(field_name='created_at', label='Data zgłoszenia (od-do)')
    machine = filters.NumberFilter(field_name='breakdown__machine__id')
    department = filters.NumberFilter(field_name='breakdown__machine__department__id')

    class Meta:
        model = BreakdownMove
        fields = ['status', 'machine', 'department', 'user']