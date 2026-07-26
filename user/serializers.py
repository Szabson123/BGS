import secrets
import string
from django.db import transaction
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import serializers

from ur.models import Workshop, CurrentWorkshop, WorkshopParticipant

User = get_user_model()


class CreateUserByAdminSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(write_only=True, required=True)
    last_name = serializers.CharField(write_only=True, required=True)
    
    workshop_id = serializers.PrimaryKeyRelatedField(
        queryset=Workshop.objects.all(),
        source='workshop',
        write_only=True,
        required=True
    )
    
    generated_username = serializers.CharField(read_only=True, source='username')

    class Meta:
        model = User
        fields = [
            'first_name', 
            'last_name', 
            'workshop_id',
            'card_code', 
            'number', 
            'main_page', 
            'generated_username',
        ]

    def _generate_unique_username(self, first_name: str, last_name: str) -> str:
        fn = first_name.strip().lower()
        ln = last_name.strip().lower()

        base_first = fn[:2] if len(fn) >= 2 else fn
        base_last = ln[-3:] if len(ln) >= 3 else ln
        
        base_username = f"{base_first}{base_last}"
        username = base_username
        counter = 2

        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        return username

    def create(self, validated_data):
        first_name = validated_data.pop('first_name')
        last_name = validated_data.pop('last_name')
        workshop = validated_data.pop('workshop')

        username = self._generate_unique_username(first_name, last_name)
        dummy_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

        with transaction.atomic():
            user = User.objects.create_user(
                username=username,
                password=dummy_password,
                first_name=first_name,
                last_name=last_name,
                is_active=False,
                is_in_change_password=True,
                **validated_data
            )

            worker_group, _ = Group.objects.get_or_create(name='ur_worker')
            user.groups.add(worker_group)

            WorkshopParticipant.objects.create(
                user=user,
                workshop=workshop
            )

            CurrentWorkshop.objects.create(
                user=user,
                workshop=workshop
            )

        return user


class FirstPasswordSetSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    password = serializers.CharField(write_only=True, required=True, min_length=4)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"password": "Hasła nie są identyczne."})

        try:
            user = User.objects.get(username=attrs['username'])
        except User.DoesNotExist:
            raise serializers.ValidationError({"username": "Użytkownik o podanym loginie nie istnieje."})

        if not user.is_in_change_password:
            raise serializers.ValidationError({"username": "Ten użytkownik ma już aktywne konto i ustawione hasło."})

        attrs['user'] = user
        return attrs

    def save(self):
        user = self.validated_data['user']
        new_password = self.validated_data['password']

        user.set_password(new_password)
        user.is_active = True
        user.is_in_change_password = False
        user.save()

        return user


class ResetUserPasswordSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=True)

    def validate_user_id(self, value):
        try:
            user = User.objects.get(id=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("Użytkownik o podanym ID nie istnieje.")
        
        return value

    def save(self):
        user_id = self.validated_data['user_id']
        user = User.objects.get(id=user_id)

        dummy_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

        user.set_password(dummy_password)
        user.is_in_change_password = True
        user.is_active = False
        user.save()

        return user