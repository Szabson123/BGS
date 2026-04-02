from django.db import models


class OperatorUser(models.Model):
    class Levels(models.TextChoices):
        P1 = 'Novice', 'P1'
        P2 = 'Beginner', 'P2'
        P3 = 'Intermediate', 'P3'
        P4 = 'Advanced', 'P4'

    name = models.CharField(max_length=255)
    surname = models.CharField(max_length=255)
    level = models.CharField(choices=Levels, max_length=255)
    active = models.BooleanField(default=True)

    card_nfc = models.CharField(max_length=255)
    card_scan = models.CharField(max_length=255)


class OperatorWorkTime(models.CharField):
    operator = models.ForeignKey(OperatorUser, on_delete=models.CASCADE)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField()
    machine = models.CharField(max_length=255) #In futere pair to Machine in other app



class ExamOperator(models.Model):
    operator = models.ForeignKey(OperatorUser, on_delete=models.CASCADE, related_name='exam') 
    time = models.DateTimeField()
    who_train = models.CharField(max_length=255) #Unknown maybe Operator other maybe user
    who_exam = models.CharField(max_length=255) #Unknown maybe Operator other maybe user