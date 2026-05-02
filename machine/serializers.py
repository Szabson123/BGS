from rest_framework import serializers

from .models import Workshop, Machine, BreakDown, BreakDownMove, MachineNotes, AdditionalEndingBreakDownInfo, ResponsibleForBreakdown, ClosingBreakdownTypes
from user.models import CustomUser


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
    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'department']


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
    description = serializers.CharField(max_length=1024, required=False)


class FullBreakDownHistory(serializers.ModelSerializer):
    reporter = UserSerializer(read_only=True)
    history = BreakDownMoveSerializer(many=True, read_only=True)

    class Meta:
        model = BreakDown
        fields = ['id', 'created_at', 'priority', 'reporter', 'description', 'history']

class MachineFullListSerializer(serializers.ModelSerializer):
    breakdowns = FullBreakDownHistory(many=True, read_only=True)
    class Meta:
        model = Machine
        fields = ['id', 'breakdowns']


class BreakDownListSerializerFullHistory(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    history = BreakDownMoveSerializer(many=True, read_only=True)

    class Meta:
        model = BreakDown
        fields = ['id', 'machine', 'created_at', 'priority', 'reporter', 'description', 'history']


class EndBreakdownSerializer(serializers.ModelSerializer):
    closing_break_down_type = serializers.PrimaryKeyRelatedField(queryset=ClosingBreakdownTypes.objects.all())
    responsible_for_breakdown = serializers.PrimaryKeyRelatedField(queryset=ResponsibleForBreakdown.objects.all())
    
    class Meta:
        model = BreakDownMove
        fields = ['break_down', 'description', 'closing_break_down_type', 'responsible_for_breakdown']
