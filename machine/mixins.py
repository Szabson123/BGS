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