from .models import OperatorUser, OperatorWorkTime, ExamOperator
from rest_framework import serializers

class ExamOperatorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamOperator
        fields = ['id', 'time', 'who_train', 'who_exam']


class OperatorUserSerializer(serializers.ModelSerializer):
    exam = ExamOperatorSerializer(many=True, read_only=True)
    class Meta:
        model = OperatorUser
        fields = ['id', 'name', 'surname', 'level', 'active', 'exam']


