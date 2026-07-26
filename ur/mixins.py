class WorkshopContextMixin:
    def get_queryset(self):
        workshop_id = self.kwargs.get('workshop_id')
        return super().get_queryset().filter(workshop_id=workshop_id)
    
    def perform_create(self, serializer):
        workshop_id = self.kwargs.get('workshop_id')
        serializer.save(workshop_id=workshop_id)


class CurrentWorkshopMixin:
    def get_workshop(self):
        if hasattr(self.request.user, 'currentworkshop'):
            return self.request.user.currentworkshop.workshop
        return None
    
    def get_queryset(self):
        workshop = self.get_workshop()
        queryset = super().get_queryset()
        
        if not workshop:
            return queryset.none()
        
        filter_kwargs = {self.workshop_lookup_field: workshop}
        return queryset.filter(**filter_kwargs)


class CurrentDepartmentsMixin:
    """
    Mixin filtrujący QuerySet po maszynach przypisanych do AKTUALNIE WYBRANYCH
    departamentów użytkownika (user.currentdepartments).
    """
    department_lookup_field = 'machine__department_id__in'

    def get_user_department_ids(self):
        user = self.request.user
        if user.is_authenticated:
            return list(user.currentdepartments.values_list('department_id', flat=True))
        return []

    def get_queryset(self):
        dept_ids = self.get_user_department_ids()
        queryset = super().get_queryset()

        if not dept_ids:
            return queryset.none()

        filter_kwargs = {self.department_lookup_field: dept_ids}
        return queryset.filter(**filter_kwargs)