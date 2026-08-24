from datetime import datetime, time
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from user.models import CustomUser
from .models import Workshop, Machine, Department, WorkSchedulePreset, ScheduleBreak
from .services import get_machine_break_status


class MachineBreakScheduleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(username='testuser', password='password123')
        self.workshop = Workshop.objects.create(name='Warsztat Główny', owner=self.user)
        self.department = Department.objects.create(name='Wytłaczarki')

        # Create Machine
        self.machine = Machine.objects.create(
            name='Wytłaczarka 1',
            workshop=self.workshop,
            department=self.department
        )

        # Preset 8h attached to machine
        self.preset_8h = WorkSchedulePreset.objects.create(
            machine=self.machine,
            name='Tryb 8-godzinny (3 zmiany)',
            shift_duration_hours=8,
            is_active=False
        )
        self.break1_8h = ScheduleBreak.objects.create(
            preset=self.preset_8h,
            name='Przerwa śniadaniowa',
            start_time=time(10, 0, 0),
            duration_minutes=15,
            order=1
        )
        self.break2_8h = ScheduleBreak.objects.create(
            preset=self.preset_8h,
            name='Przerwa obiadowa',
            start_time=time(14, 0, 0),
            duration_minutes=20,
            order=2
        )

        # Preset 12h attached to machine
        self.preset_12h = WorkSchedulePreset.objects.create(
            machine=self.machine,
            name='Tryb 12-godzinny (2 zmiany)',
            shift_duration_hours=12,
            is_active=False
        )
        self.break1_12h = ScheduleBreak.objects.create(
            preset=self.preset_12h,
            name='Przerwa 1',
            start_time=time(9, 30, 0),
            duration_minutes=20,
            order=1
        )
        self.break2_12h = ScheduleBreak.objects.create(
            preset=self.preset_12h,
            name='Przerwa 2',
            start_time=time(14, 0, 0),
            duration_minutes=30,
            order=2
        )

    def test_no_schedule_active(self):
        result = get_machine_break_status(self.machine)
        self.assertFalse(result['is_on_break'])
        self.assertIsNone(result['current_break'])
        self.assertIsNone(result['next_break'])
        self.assertIsNone(result['current_schedule'])

    def test_break_status_before_break(self):
        self.preset_8h.is_active = True
        self.preset_8h.save()

        # Simulated time: 09:30:00 (30 minutes before 10:00:00 break)
        test_dt = timezone.make_aware(datetime(2026, 8, 24, 9, 30, 0))
        result = get_machine_break_status(self.machine, current_dt=test_dt)

        self.assertFalse(result['is_on_break'])
        self.assertIsNone(result['current_break'])
        self.assertIsNotNone(result['next_break'])
        self.assertEqual(result['next_break']['name'], 'Przerwa śniadaniowa')
        self.assertEqual(result['next_break']['starts_in_seconds'], 1800)
        self.assertEqual(result['next_break']['starts_in_minutes'], 30)

    def test_break_status_during_break(self):
        self.preset_8h.is_active = True
        self.preset_8h.save()

        # Simulated time: 10:05:00 (5 minutes into 15-minute break)
        test_dt = timezone.make_aware(datetime(2026, 8, 24, 10, 5, 0))
        result = get_machine_break_status(self.machine, current_dt=test_dt)

        self.assertTrue(result['is_on_break'])
        self.assertIsNotNone(result['current_break'])
        self.assertEqual(result['current_break']['name'], 'Przerwa śniadaniowa')
        self.assertEqual(result['current_break']['remaining_seconds'], 600)
        self.assertEqual(result['current_break']['remaining_minutes'], 10)
        
        # Next break should be break2_8h at 14:00
        self.assertIsNotNone(result['next_break'])
        self.assertEqual(result['next_break']['name'], 'Przerwa obiadowa')

    def test_break_status_after_all_today_breaks(self):
        self.preset_8h.is_active = True
        self.preset_8h.save()

        # Simulated time: 20:00:00 (after 10:00 and 14:00)
        test_dt = timezone.make_aware(datetime(2026, 8, 24, 20, 0, 0))
        result = get_machine_break_status(self.machine, current_dt=test_dt)

        self.assertFalse(result['is_on_break'])
        self.assertIsNone(result['current_break'])
        self.assertIsNotNone(result['next_break'])
        self.assertEqual(result['next_break']['name'], 'Przerwa śniadaniowa')
        # Starts tomorrow at 10:00 (14 hours later = 14 * 3600 = 50400 s)
        self.assertEqual(result['next_break']['starts_in_seconds'], 50400)

    def test_switch_to_12h_preset_endpoint(self):
        self.client.force_authenticate(user=self.user)

        # 1. Set machine to 12h preset
        response = self.client.post(
            f'/api/ur/machines/{self.machine.id}/set-schedule/',
            {'schedule_preset_id': self.preset_12h.id},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.preset_12h.refresh_from_db()
        self.preset_8h.refresh_from_db()
        self.assertTrue(self.preset_12h.is_active)
        self.assertFalse(self.preset_8h.is_active)

        # 2. Query break status endpoint
        response = self.client.get(f'/api/ur/machines/{self.machine.id}/break-status/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['current_schedule']['id'], self.preset_12h.id)
        self.assertEqual(len(data['all_breaks_today']), 2)

    def test_preset_and_breaks_crud_endpoints(self):
        self.client.force_authenticate(user=self.user)

        # 1. Create a new custom preset (e.g. 6h) for machine
        res = self.client.post('/api/ur/schedule-presets/', {
            'machine': self.machine.id,
            'name': 'Tryb 6-godzinny (Specjalny)',
            'description': 'Krótka zmiana',
            'shift_duration_hours': 6,
            'is_active': False
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        preset_id = res.json()['id']

        # 2. Add a break to this preset
        res_break = self.client.post('/api/ur/schedule-breaks/', {
            'preset': preset_id,
            'name': 'Przerwa kawowa',
            'start_time': '11:00:00',
            'duration_minutes': 10,
            'order': 1
        }, format='json')
        self.assertEqual(res_break.status_code, status.HTTP_201_CREATED)
        break_id = res_break.json()['id']

        # 3. Retrieve preset with breaks
        res_get = self.client.get(f'/api/ur/schedule-presets/{preset_id}/')
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_get.json()['breaks']), 1)
        self.assertEqual(res_get.json()['breaks'][0]['name'], 'Przerwa kawowa')

        # 4. Update break
        res_patch = self.client.patch(f'/api/ur/schedule-breaks/{break_id}/', {
            'duration_minutes': 15
        }, format='json')
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_patch.json()['duration_minutes'], 15)

        # 5. Activate preset via preset activate endpoint
        res_act = self.client.post(f'/api/ur/schedule-presets/{preset_id}/activate/')
        self.assertEqual(res_act.status_code, status.HTTP_200_OK)
        self.assertTrue(res_act.json()['is_active'])

        # 6. Check machine break status
        response = self.client.get(f'/api/ur/machines/{self.machine.id}/break-status/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['current_schedule']['id'], preset_id)


