class WorkshopContextMixin:
    def get_queryset(self):
        workshop_id = self.kwargs.get('workshop_id')
        return super().get_queryset().filter(workshop_id=workshop_id)
    
    def perform_create(self, serializer):
        workshop_id = self.kwargs.get('workshop_id')
        serializer.save(workshop_id=workshop_id)