from django_filters import rest_framework as filters
from .models import BreakDown, BreakDownMove
from django.db.models import Subquery, OuterRef
from user.models import CustomUser
from django.db import models


class BreakDownFilter(filters.FilterSet):
    date_range = filters.DateTimeFromToRangeFilter(field_name='created_at', label='Data zgłoszenia (od-do)')

    status = filters.ChoiceFilter(
        choices=BreakDownMove.Status.choices,
        method='filter_by_last_status',
        label='Zawiera status w historii'
    )

    technician = filters.ModelChoiceFilter(
        field_name='history__user',
        queryset=CustomUser.objects.all(),
        label='Serwisant biorący udział'
    )

    search = filters.CharFilter(method='filter_by_all_descriptions', label='Szukaj w opisach')

    class Meta:
        model = BreakDown
        fields = ['priority', 'reporter', 'machine']

    def filter_by_all_descriptions(self, queryset, name, value):
        return queryset.filter(
            models.Q(description__icontains=value) | 
            models.Q(history__description__icontains=value)
        ).distinct()
    
    def filter_by_last_status(self, queryset, name, value):
        latest_status_subquery = BreakDownMove.objects.filter(
            break_down=OuterRef('pk')
        ).order_by('-created_at').values('status')[:1]

        return queryset.annotate(
            last_status_val=Subquery(latest_status_subquery)
        ).filter(last_status_val=value)