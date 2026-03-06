from rest_framework import serializers

from .models import Workshop, Machine, BreakDown, BreakDownMove
from user.models import CustomUser

class WorkshopSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workshop
        fields = ['id', 'name']


class MachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias']


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'first_name', 'last_name', 'number']


class BreakDownMoveSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    class Meta:
        model = BreakDownMove
        fields = ['status', 'user']


class BreakDownListSerializer(serializers.ModelSerializer):
    machine = MachineSerializer(read_only=True)
    reporter = UserSerializer(read_only=True)
    last_status = serializers.SerializerMethodField()

    class Meta:
        model = BreakDown
        fields = ['id', 'machine', 'date_added', 'priority', 'reporter', 'description', 'last_status']

    def get_last_status(self, obj):
        last_move = obj.history.all()[:1]
        if last_move:
            return BreakDownMoveSerializer(last_move[0]).data
        return None

