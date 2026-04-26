from django_filters import rest_framework as filters
from .models import BreakDown, BreakDownMove
from user.models import CustomUser
from django.db import models


class BreakDownFilter(filters.FilterSet):
    date_range = filters.DateFromToRangeFilter(field_name='date_added', label='Data zgłoszenia (od-do)')
    status = filters.ChoiceFilter(
        field_name='history__status',
        choices=BreakDownMove.Status.choices,
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
        fields = ['machine', 'priority', 'reporter']

    def filter_by_all_descriptions(self, queryset, name, value):
        return queryset.filter(
            models.Q(description__icontains=value) | 
            models.Q(history__description__icontains=value)
        ).distinct()