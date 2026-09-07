from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from .models import (Workshop, CurrentWorkshop, Machine, Breakdown, BreakdownMove, MachineNotes, 
                     AdditionalEndingBreakdownInfo, ResponsibleForBreakdown, ClosingBreakdownTypes, 
                     Department, WorkshopParticipant, WorkSchedulePreset, ScheduleBreak)
from user.models import CustomUser


class LookupOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()

class ChoicesOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class ScheduleBreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleBreak
        fields = ['id', 'preset', 'name', 'start_time', 'duration_minutes', 'order']
        extra_kwargs = {'preset': {'required': False}}


class WorkSchedulePresetSerializer(serializers.ModelSerializer):
    breaks = ScheduleBreakSerializer(many=True, read_only=True)
    machine_name = serializers.StringRelatedField(source='machine.name', read_only=True)

    class Meta:
        model = WorkSchedulePreset
        fields = ['id', 'machine', 'machine_name', 'name', 'description', 'shift_duration_hours', 'is_active', 'breaks']
        extra_kwargs = {'machine': {'required': False}}


class MachineSetScheduleSerializer(serializers.Serializer):
    schedule_preset_id = serializers.IntegerField(required=False, allow_null=True)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'first_name', 'last_name', 'number', 'is_active', 'username']

class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name']


class AdditionalEndingBreakdownInfoSerializer(serializers.ModelSerializer):
    closing_breakdown_type_name = serializers.StringRelatedField(source='closing_breakdown_type.name')
    responsible_for_breakdown = serializers.StringRelatedField(source='responsible_for_breakdown.name')
    class Meta:
        model = AdditionalEndingBreakdownInfo
        fields = ['closing_breakdown_type_name', 'responsible_for_breakdown']


class ClosingBreakdownTypesSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClosingBreakdownTypes
        fields = ['id', 'name']


class ResponsibleForBreakdownSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResponsibleForBreakdown
        fields = ['id', 'name']


class WorkshopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workshop
        fields = ['id', 'name']


class MachineSerializer(serializers.ModelSerializer):
    active_schedule_name = serializers.SerializerMethodField()
    active_schedule_id = serializers.SerializerMethodField()

    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'sigip_num', 'active_schedule_id', 'active_schedule_name']

    def get_active_schedule_name(self, obj):
        active = obj.schedules.filter(is_active=True).first()
        return active.name if active else None

    def get_active_schedule_id(self, obj):
        active = obj.schedules.filter(is_active=True).first()
        return active.id if active else None


class MachineNotesSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    class Meta:
        model = MachineNotes
        fields = ['id', 'description', 'file', 'created_by', 'created_at']


class MachineMainSerializer(serializers.ModelSerializer):
    department_name = serializers.StringRelatedField(source='department.name', read_only=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    workshop_name = serializers.StringRelatedField(source='workshop.name', read_only=True)
    active_schedule_name = serializers.SerializerMethodField()
    active_schedule_id = serializers.SerializerMethodField()
    schedules = WorkSchedulePresetSerializer(many=True, read_only=True)

    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'phase_id', 'sigip_num', 'department_name', 'department', 'workshop_name', 'active_schedule_id', 'active_schedule_name', 'schedules']

    def get_active_schedule_name(self, obj):
        active = obj.schedules.filter(is_active=True).first()
        return active.name if active else None

    def get_active_schedule_id(self, obj):
        active = obj.schedules.filter(is_active=True).first()
        return active.id if active else None


class BreakdownMoveSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = BreakdownMove
        fields = ['status', 'user', 'description', 'created_at']


class BreakdownListSerializer(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    latest_status = serializers.SerializerMethodField()

    class Meta:
        model = Breakdown
        fields = ['id', 'machine', 'created_at', 'priority', 'reporter', 'description', 'latest_status']

    def get_latest_status(self, obj):
        status = getattr(obj, 'current_status_list', [])
        if status:
            return BreakdownMoveSerializer(status[0]).data
        return None


class BreakdownCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Breakdown
        fields = ['machine', 'priority', 'description']


class BreakdownMovePostSerializer(serializers.Serializer):
    status = serializers.CharField(max_length=255, required=True)
    breakdown = serializers.PrimaryKeyRelatedField(queryset=Breakdown.objects.all())
    description = serializers.CharField(max_length=1024, required=False, allow_blank=True)


class FullBreakdownHistorySerializer(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    history = BreakdownMoveSerializer(many=True, read_only=True)

    class Meta:
        model = Breakdown
        fields = ['id', 'created_at', 'priority', 'reporter', 'description', 'history']


class MachineFullListSerializer(serializers.ModelSerializer):
    Breakdowns = FullBreakdownHistorySerializer(many=True, read_only=True)
    class Meta:
        model = Machine
        fields = ['id', 'Breakdowns']


class BreakdownListSerializerFullHistory(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    history = BreakdownMoveSerializer(many=True, read_only=True)
    additional = AdditionalEndingBreakdownInfoSerializer(read_only=True)

    class Meta:
        model = Breakdown
        fields = ['id', 'machine', 'created_at', 'priority', 'reporter', 'description', 'history', 'additional']


class BreakdownInfoToMovesSerializer(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    class Meta:
        model = Breakdown
        fields = ['id', 'priority', 'description', 'reporter']


class EndBreakdownSerializer(serializers.ModelSerializer):
    closing_breakdown_type = serializers.PrimaryKeyRelatedField(queryset=ClosingBreakdownTypes.objects.all())
    responsible_for_breakdown = serializers.PrimaryKeyRelatedField(queryset=ResponsibleForBreakdown.objects.all())
    
    class Meta:
        model = BreakdownMove
        fields = ['breakdown', 'description', 'closing_breakdown_type', 'responsible_for_breakdown']


class BreakdownMoveToHistorySerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    machine = MachineMainSerializer(source='breakdown.machine', read_only=True)
    breakdown = BreakdownInfoToMovesSerializer(read_only=True)
    class Meta:
        model = BreakdownMove
        fields = ['id', 'user', 'status', 'description', 'created_at', 'machine', 'breakdown']


class BreakdownOptionsResponseSerializer(serializers.Serializer):
    priorities = ChoicesOptionSerializer(many=True)
    statuses = ChoicesOptionSerializer(many=True)

    machines = LookupOptionSerializer(many=True)
    close_types = LookupOptionSerializer(many=True)
    responsibles = LookupOptionSerializer(many=True)


class BreakdownMoveOptionResponseSerializer(serializers.Serializer):
    departments = LookupOptionSerializer(many=True)
    machines = LookupOptionSerializer(many=True)
    users = UserSerializer(many=True)
    statuses = ChoicesOptionSerializer(many=True)


class CurrentWorkshopSerializer(serializers.ModelSerializer):
    workshop = WorkshopSerializer(read_only=True)
    class Meta:
        model = CurrentWorkshop
        fields = ['id', 'workshop']


class URProfilePanelSerializer(serializers.Serializer):
    current_workshop = CurrentWorkshopSerializer(many=False)
    avaible_workshops = WorkshopSerializer(many=True)
    user = UserSerializer(many=False)


class EndBreakdownOptionsSerializer(serializers.Serializer):
    close_types = LookupOptionSerializer(many=True)
    responsibles = LookupOptionSerializer(many=True)


class WorkshopParticipantSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(), write_only=True)
    user_full_name = UserSerializer(read_only=True, source='user')

    class Meta:
        model = WorkshopParticipant
        fields = ['id', 'user', 'user_full_name', 'workshop']
        extra_kwargs = {'workshop': {'read_only': True}}

    def validate(self, data):
        workshop_id = self.context['view'].kwargs.get('workshop_id')
        user = data.get('user')

        if WorkshopParticipant.objects.filter(workshop_id=workshop_id, user=user).exists():
            raise serializers.ValidationError(
                {"user": "Ten użytkownik już należy do warsztatu"}
            )
        
        return data


class DepartmentToggleSerializer(serializers.ModelSerializer):
    is_selected = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'name', 'is_selected']

    def get_is_selected(self, obj):
        user = self.context['request'].user
        if not user.is_authenticated:
            return False
        
        selected_department_ids = self.context.get('selected_department_ids', set())
        return obj.id in selected_department_ids


