from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from .models import (Workshop, CurrentWorkshop, Machine, Breakdown, BreakdownMove, MachineNotes, MachineNoteFile,
                     AdditionalEndingBreakdownInfo, ResponsibleForBreakdown, ClosingBreakdownTypes, 
                     Department, WorkshopParticipant, WorkSchedulePreset, ScheduleBreak, CurrentDepartment, MachineLineName, ProductionTeam)
from user.models import CustomUser
from django.db import transaction

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


class TeamRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionTeam
        fields = ['id', 'name']


class ProductionWorkerSerializer(UserSerializer):
    teams = TeamRefSerializer(source='production_teams', many=True, read_only=True)

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ['teams']


class ProductionTeamSerializer(serializers.ModelSerializer):
    supervisors = UserSerializer(many=True, read_only=True)
    members = UserSerializer(many=True, read_only=True)
    supervisor_ids = serializers.PrimaryKeyRelatedField(
        source='supervisors',
        many=True,
        write_only=True,
        required=False,
        queryset=CustomUser.objects.filter(groups__name='ur_production_supervisor').distinct(),
    )
    member_ids = serializers.PrimaryKeyRelatedField(
        source='members',
        many=True,
        write_only=True,
        required=False,
        queryset=CustomUser.objects.filter(groups__name='ur_production').distinct(),
    )

    class Meta:
        model = ProductionTeam
        fields = ['id', 'name', 'description', 'supervisors', 'members', 'supervisor_ids', 'member_ids']


class ProductionTeamTransferSerializer(serializers.Serializer):
    from_user_id = serializers.PrimaryKeyRelatedField(source='from_user', queryset=CustomUser.objects.all())
    to_user_id = serializers.PrimaryKeyRelatedField(
        source='to_user',
        queryset=CustomUser.objects.filter(groups__name='ur_production_supervisor').distinct(),
    )
    team_ids = serializers.PrimaryKeyRelatedField(
        source='teams',
        many=True,
        required=False,
        queryset=ProductionTeam.objects.all(),
    )

    def validate(self, attrs):
        from_user = attrs['from_user']
        to_user = attrs['to_user']
        if from_user == to_user:
            raise serializers.ValidationError("Wybierz dwie różne osoby.")

        teams = attrs.get('teams')
        if teams:
            supervised_ids = set(from_user.supervised_production_teams.values_list('id', flat=True))
            foreign = [team.name for team in teams if team.id not in supervised_ids]
            if foreign:
                raise serializers.ValidationError(
                    {"team_ids": f"Te zespoły nie należą do wybranej osoby: {', '.join(foreign)}"}
                )
        return attrs

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


class MachineLineNameSerializer(serializers.ModelSerializer):
    class Meta:
        model = MachineLineName
        fields = ['id', 'name']

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
    line = MachineLineNameSerializer(read_only=True)
    active_schedule_name = serializers.SerializerMethodField()
    active_schedule_id = serializers.SerializerMethodField()

    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'sigip_num', 'line', 'active_schedule_id', 'active_schedule_name']

    def get_active_schedule_name(self, obj):
        active = next((s for s in obj.schedules.all() if s.is_active), None)
        return active.name if active else None

    def get_active_schedule_id(self, obj):
        active = next((s for s in obj.schedules.all() if s.is_active), None)
        return active.id if active else None

class MachineNoteFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MachineNoteFile
        fields = ['id', 'file', 'file_name', 'created_at']


class MachineNotesSerializer(serializers.ModelSerializer):
    created_by = UserSerializer(read_only=True)
    files = serializers.SerializerMethodField()

    class Meta:
        model = MachineNotes
        fields = ['id', 'description', 'file', 'files', 'created_by', 'created_at']

    def get_files(self, obj):
        request = self.context.get('request')
        result = []
        # Stary plik dla kompatybilności wstecznej jeśli istnieje
        if obj.file:
            file_url = request.build_absolute_uri(obj.file.url) if request else obj.file.url
            result.append({
                'id': f'legacy_{obj.id}',
                'file': file_url,
                'file_name': obj.file.name.split('/')[-1]
            })

        for note_file in obj.files.all():
            if note_file.file:
                file_url = request.build_absolute_uri(note_file.file.url) if request else note_file.file.url
                result.append({
                    'id': note_file.id,
                    'file': file_url,
                    'file_name': note_file.file_name or note_file.file.name.split('/')[-1]
                })
        return result


class MachineMainSerializer(serializers.ModelSerializer):
    department_name = serializers.StringRelatedField(source='department.name', read_only=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all())
    workshop_name = serializers.StringRelatedField(source='workshop.name', read_only=True)
    active_schedule_name = serializers.SerializerMethodField()
    active_schedule_id = serializers.SerializerMethodField()
    schedules = WorkSchedulePresetSerializer(many=True, read_only=True)

    # Linia
    line = MachineLineNameSerializer(read_only=True)
    line_id = serializers.PrimaryKeyRelatedField(
        queryset=MachineLineName.objects.all(),
        source='line', write_only=True, required=False, allow_null=True
    )
    line_name = serializers.CharField(
        write_only=True, required=False, allow_blank=True, allow_null=True
    )

    class Meta:
        model = Machine
        fields = ['id', 'name', 'alias', 'phase_id', 'sigip_num', 'department_name', 'department',
                  'workshop_name', 'line', 'line_id', 'line_name',
                  'active_schedule_id', 'active_schedule_name', 'schedules']

    def _resolve_line(self, validated_data):
        """line_name ma pierwszeństwo: istniejąca linia jest podpinana, nowa tworzona."""
        if 'line_name' not in validated_data:
            return
        name = (validated_data.pop('line_name') or '').strip()
        if not name:
            # Pusty line_name odpina linię tylko wtedy, gdy nie podano line_id
            validated_data.setdefault('line', None)
            return
        line = MachineLineName.objects.filter(name__iexact=name).first()
        if line is None:
            line = MachineLineName.objects.create(name=name)
        validated_data['line'] = line

    def create(self, validated_data):
        with transaction.atomic():
            self._resolve_line(validated_data)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            self._resolve_line(validated_data)
            return super().update(instance, validated_data)

    def get_active_schedule_name(self, obj):
        active = self._get_active_schedule(obj)
        return active.name if active else None

    def get_active_schedule_id(self, obj):
        active = self._get_active_schedule(obj)
        return active.id if active else None

    def _get_active_schedule(self, obj):
        # Iteracja po .all() korzysta z prefetch_related; .filter() robiłby nowe zapytanie dla każdej maszyny
        return next((s for s in obj.schedules.all() if s.is_active), None)

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
    lines = LookupOptionSerializer(many=True)
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


class DepartmentParticipantSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all(), write_only=True)
    user_full_name = UserSerializer(read_only=True, source='user')

    class Meta:
        model = CurrentDepartment
        fields = ['id', 'user', 'user_full_name', 'department']
        extra_kwargs = {'department': {'read_only': True}}

    def validate(self, data):
        department_id = self.context['view'].kwargs.get('department_id')
        user = data.get('user')

        if CurrentDepartment.objects.filter(department_id=department_id, user=user).exists():
            raise serializers.ValidationError(
                {"user": "Ten użytkownik jest już przypisany do tego działu"}
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


