from rest_framework import serializers

from .models import Workshop, Machine, BreakDown, BreakDownMove, MachineNotes, AdditionalEndingBreakDownInfo, ResponsibleForBreakdown, ClosingBreakdownTypes, Department
from user.models import CustomUser


class LookupOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()

class ChoicesOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class DepartamentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name']


class AdditionalEndingBreakDownInfoSerializer(serializers.ModelSerializer):
    closing_break_down_type_name = serializers.StringRelatedField(source='closing_break_down_type.name')
    responsible_for_breakdown = serializers.StringRelatedField(source='responsible_for_breakdown.name')
    class Meta:
        model = AdditionalEndingBreakDownInfo
        fields = ['closing_break_down_type_name', 'responsible_for_breakdown']


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
    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias']


class MachineNotesSerializer(serializers.ModelSerializer):
    class Meta:
        model = MachineNotes
        fields = ['id', 'description']


class MachineMainSerializer(serializers.ModelSerializer):
    department_name = serializers.StringRelatedField(source='department.name', read_only=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    workshop_name = serializers.StringRelatedField(source='workshop.name', read_only=True)

    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'phase_id', 'sigip_num', 'department_name', 'department', 'workshop_name']


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'first_name', 'last_name', 'number']


class BreakDownMoveSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = BreakDownMove
        fields = ['status', 'user', 'description', 'created_at']


class BreakDownListSerializer(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    latest_status = serializers.SerializerMethodField()

    class Meta:
        model = BreakDown
        fields = ['id', 'machine', 'created_at', 'priority', 'reporter', 'description', 'latest_status']

    def get_latest_status(self, obj):
        status = getattr(obj, 'current_status_list', [])
        if status:
            return BreakDownMoveSerializer(status[0]).data
        return None


class BreakDownCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BreakDown
        fields = ['machine', 'priority', 'description']


class BreakDownMovePostSerializer(serializers.Serializer):
    status = serializers.CharField(max_length=255, required=True)
    break_down = serializers.PrimaryKeyRelatedField(queryset=BreakDown.objects.all())
    description = serializers.CharField(max_length=1024, required=False, allow_blank=True)


class FullBreakDownHistorySerializer(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    history = BreakDownMoveSerializer(many=True, read_only=True)

    class Meta:
        model = BreakDown
        fields = ['id', 'created_at', 'priority', 'reporter', 'description', 'history']


class MachineFullListSerializer(serializers.ModelSerializer):
    breakdowns = FullBreakDownHistorySerializer(many=True, read_only=True)
    class Meta:
        model = Machine
        fields = ['id', 'breakdowns']


class BreakDownListSerializerFullHistory(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    history = BreakDownMoveSerializer(many=True, read_only=True)
    additional = AdditionalEndingBreakDownInfoSerializer(read_only=True)

    class Meta:
        model = BreakDown
        fields = ['id', 'machine', 'created_at', 'priority', 'reporter', 'description', 'history', 'additional']


class BreakDownInfoToMovesSerializer(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    class Meta:
        model = BreakDown
        fields = ['id', 'priority', 'description', 'reporter']


class EndBreakdownSerializer(serializers.ModelSerializer):
    closing_break_down_type = serializers.PrimaryKeyRelatedField(queryset=ClosingBreakdownTypes.objects.all())
    responsible_for_breakdown = serializers.PrimaryKeyRelatedField(queryset=ResponsibleForBreakdown.objects.all())
    
    class Meta:
        model = BreakDownMove
        fields = ['break_down', 'description', 'closing_break_down_type', 'responsible_for_breakdown']


class BreakDownMoveToHistorySerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    machine = MachineMainSerializer(source='break_down.machine', read_only=True)
    break_down = BreakDownInfoToMovesSerializer(read_only=True)
    class Meta:
        model = BreakDownMove
        fields = ['id', 'user', 'status', 'description', 'created_at', 'machine', 'break_down']


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