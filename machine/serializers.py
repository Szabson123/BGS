from rest_framework import serializers

from .models import Workshop, Machine, BreakDown, BreakDownMove, MachineNotes
from user.models import CustomUser

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
        fields = ['id', 'name', 'alias', 'workshop']


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'first_name', 'last_name', 'number']


class BreakDownMoveSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = BreakDownMove
        fields = ['status', 'user', 'description', 'time']


class BreakDownListSerializer(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    latest_status = serializers.SerializerMethodField()

    class Meta:
        model = BreakDown
        fields = ['id', 'machine', 'date_added', 'priority', 'reporter', 'description', 'latest_status']

    def get_latest_status(self, obj):
        status = getattr(obj, 'latest_status', [])
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
        fields = ['id', 'date_added', 'priority', 'reporter', 'description', 'history']

class MachineFullListSerializer(serializers.ModelSerializer):
    notes = MachineNotesSerializer(many=True, read_only=True)
    breakdowns = FullBreakDownHistory(many=True, read_only=True)
    class Meta:
        model = Machine
        fields = ['id', 'notes', 'breakdowns']
