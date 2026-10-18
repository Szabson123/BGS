from datetime import datetime, time
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from user.models import CustomUser
from django.contrib.auth.models import Group
from .models import (Workshop, Machine, Department, WorkSchedulePreset, ScheduleBreak, ProductionTeam,
                     CurrentDepartment, Breakdown, ResponsibleForBreakdown)
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


class CurrentDepartmentScheduleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(username='deptuser', password='password123')
        self.workshop = Workshop.objects.create(name='Warsztat UR', owner=self.user)

        # 3 Departments
        self.dept1 = Department.objects.create(name='Wytłaczarki')
        self.dept2 = Department.objects.create(name='Wtryskarki')
        self.dept3 = Department.objects.create(name='Pakowanie')

        # Machines in departments
        self.machine_dept1 = Machine.objects.create(
            name='Wytłaczarka #1',
            workshop=self.workshop,
            department=self.dept1
        )
        self.machine_dept2 = Machine.objects.create(
            name='Wtryskarka #1',
            workshop=self.workshop,
            department=self.dept2
        )
        self.machine_dept3 = Machine.objects.create(
            name='Pakowarka #1',
            workshop=self.workshop,
            department=self.dept3
        )

        # Presets for machines
        self.preset_dept1 = WorkSchedulePreset.objects.create(
            machine=self.machine_dept1,
            name='Harmonogram Wytłaczarka',
            shift_duration_hours=8,
            is_active=True
        )
        self.break_dept1 = ScheduleBreak.objects.create(
            preset=self.preset_dept1,
            name='Przerwa Wytłaczarki',
            start_time=time(10, 0),
            duration_minutes=15
        )

        self.preset_dept2 = WorkSchedulePreset.objects.create(
            machine=self.machine_dept2,
            name='Harmonogram Wtryskarka',
            shift_duration_hours=12,
            is_active=True
        )
        self.break_dept2 = ScheduleBreak.objects.create(
            preset=self.preset_dept2,
            name='Przerwa Wtryskarki',
            start_time=time(11, 0),
            duration_minutes=20
        )

        self.preset_dept3 = WorkSchedulePreset.objects.create(
            machine=self.machine_dept3,
            name='Harmonogram Pakowanie',
            shift_duration_hours=8,
            is_active=True
        )
        self.break_dept3 = ScheduleBreak.objects.create(
            preset=self.preset_dept3,
            name='Przerwa Pakowanie',
            start_time=time(12, 0),
            duration_minutes=30
        )

    def test_machines_in_current_departments_empty_when_no_dept(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/ur/machines-to-current-departments/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()), 0)

    def test_machines_in_current_departments_single_department(self):
        from .models import CurrentDepartment
        CurrentDepartment.objects.create(user=self.user, department=self.dept1)

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/ur/machines-to-current-departments/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        machines = response.json()
        self.assertEqual(len(machines), 1)
        self.assertEqual(machines[0]['id'], self.machine_dept1.id)

        # Check presets endpoint
        res_presets = self.client.get('/api/ur/schedule-presets/')
        self.assertEqual(res_presets.status_code, status.HTTP_200_OK)
        presets = res_presets.json()
        self.assertEqual(len(presets), 1)
        self.assertEqual(presets[0]['id'], self.preset_dept1.id)

        # Check breaks endpoint
        res_breaks = self.client.get('/api/ur/schedule-breaks/')
        self.assertEqual(res_breaks.status_code, status.HTTP_200_OK)
        breaks = res_breaks.json()
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]['id'], self.break_dept1.id)

    def test_machines_in_current_departments_multiple_departments(self):
        from .models import CurrentDepartment
        CurrentDepartment.objects.create(user=self.user, department=self.dept1)
        CurrentDepartment.objects.create(user=self.user, department=self.dept2)

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/ur/machines-to-current-departments/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        machines = response.json()
        self.assertEqual(len(machines), 2)
        machine_ids = {m['id'] for m in machines}
        self.assertIn(self.machine_dept1.id, machine_ids)
        self.assertIn(self.machine_dept2.id, machine_ids)
        self.assertNotIn(self.machine_dept3.id, machine_ids)

        # Check presets endpoint filters by the 2 departments
        res_presets = self.client.get('/api/ur/schedule-presets/')
        self.assertEqual(res_presets.status_code, status.HTTP_200_OK)
        presets = res_presets.json()
        preset_ids = {p['id'] for p in presets}
        self.assertIn(self.preset_dept1.id, preset_ids)
        self.assertIn(self.preset_dept2.id, preset_ids)
        self.assertNotIn(self.preset_dept3.id, preset_ids)


class ProductionTeamTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        prod_group, _ = Group.objects.get_or_create(name='ur_production')
        sup_group, _ = Group.objects.get_or_create(name='ur_production_supervisor')
        admin_group, _ = Group.objects.get_or_create(name='ur_admin')

        self.admin = CustomUser.objects.create_user(username='admin', password='x', is_active=True)
        self.admin.groups.add(admin_group)

        self.sup_a = CustomUser.objects.create_user(username='sup_a', password='x', is_active=True)
        self.sup_b = CustomUser.objects.create_user(username='sup_b', password='x', is_active=True)
        self.sup_c = CustomUser.objects.create_user(username='sup_c', password='x', is_active=True)
        for sup in (self.sup_a, self.sup_b, self.sup_c):
            sup.groups.add(sup_group)

        def worker(username):
            user = CustomUser.objects.create_user(username=username, password='x', is_active=True)
            user.groups.add(prod_group)
            return user

        self.worker_a = worker('worker_a')
        self.worker_b = worker('worker_b')
        self.worker_free = worker('worker_free')

        self.team_a = ProductionTeam.objects.create(name='Zespół A')
        self.team_a.supervisors.add(self.sup_a)
        self.team_a.members.add(self.worker_a)

        self.team_b = ProductionTeam.objects.create(name='Zespół B')
        self.team_b.supervisors.add(self.sup_b)
        self.team_b.members.add(self.worker_b)

        self.team_a2 = ProductionTeam.objects.create(name='Zespół A2')
        self.team_a2.supervisors.add(self.sup_a)

    def _worker_ids(self):
        res = self.client.get('/api/ur/production-workers/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return {w['id'] for w in res.json()}

    def test_supervisor_sees_own_team_and_unassigned_workers(self):
        self.client.force_authenticate(self.sup_a)
        self.assertEqual(self._worker_ids(), {self.worker_a.id, self.worker_free.id})

    def test_admin_sees_all_workers_with_teams(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/ur/production-workers/')
        by_id = {w['id']: w for w in res.json()}
        self.assertEqual(set(by_id), {self.worker_a.id, self.worker_b.id, self.worker_free.id})
        self.assertEqual(by_id[self.worker_a.id]['teams'], [{'id': self.team_a.id, 'name': 'Zespół A'}])

    def test_supervisor_cannot_delete_other_team_worker(self):
        self.client.force_authenticate(self.sup_a)
        res = self.client.delete(f'/api/ur/production-workers/{self.worker_b.id}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(CustomUser.objects.filter(id=self.worker_b.id).exists())

    def test_supervisor_lists_only_own_teams(self):
        self.client.force_authenticate(self.sup_a)
        res = self.client.get('/api/ur/production-teams/')
        self.assertEqual({t['id'] for t in res.json()}, {self.team_a.id, self.team_a2.id})
        res_other = self.client.get(f'/api/ur/production-teams/{self.team_b.id}/')
        self.assertEqual(res_other.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_admin_creates_and_deletes_teams(self):
        self.client.force_authenticate(self.sup_a)
        res = self.client.post('/api/ur/production-teams/', {'name': 'Nowy'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        res = self.client.delete(f'/api/ur/production-teams/{self.team_a.id}/')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/ur/production-teams/', {
            'name': 'Nowy',
            'supervisor_ids': [self.sup_c.id],
            'member_ids': [self.worker_free.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        team = ProductionTeam.objects.get(name='Nowy')
        self.assertEqual(list(team.supervisors.all()), [self.sup_c])
        self.assertEqual(list(team.members.all()), [self.worker_free])

    def test_supervisor_can_change_members_of_own_team_only(self):
        self.client.force_authenticate(self.sup_a)
        res = self.client.patch(f'/api/ur/production-teams/{self.team_a.id}/', {
            'member_ids': [self.worker_a.id, self.worker_free.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(set(self.team_a.members.values_list('id', flat=True)), {self.worker_a.id, self.worker_free.id})

        res = self.client.patch(f'/api/ur/production-teams/{self.team_a.id}/', {'name': 'Zmiana'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        res = self.client.patch(f'/api/ur/production-teams/{self.team_a.id}/', {
            'member_ids': [self.worker_a.id, self.worker_b.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.team_a.members.filter(id=self.worker_b.id).exists())

    def test_admin_transfers_all_teams(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id,
            'to_user_id': self.sup_c.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(set(res.json()['moved_team_ids']), {self.team_a.id, self.team_a2.id})
        self.assertFalse(self.sup_a.supervised_production_teams.exists())
        self.assertEqual(set(self.sup_c.supervised_production_teams.values_list('id', flat=True)),
                         {self.team_a.id, self.team_a2.id})

        # Nowy supervisor od razu widzi pracowników przejętego zespołu
        self.client.force_authenticate(self.sup_c)
        self.assertIn(self.worker_a.id, self._worker_ids())

    def test_admin_transfers_selected_teams(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id,
            'to_user_id': self.sup_c.id,
            'team_ids': [self.team_a2.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(list(self.sup_a.supervised_production_teams.all()), [self.team_a])
        self.assertEqual(list(self.sup_c.supervised_production_teams.all()), [self.team_a2])

    def test_transfer_validation_and_permissions(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id, 'to_user_id': self.sup_a.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id, 'to_user_id': self.sup_c.id, 'team_ids': [self.team_b.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id, 'to_user_id': self.worker_free.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(self.sup_a)
        res = self.client.post('/api/ur/production-teams/transfer/', {
            'from_user_id': self.sup_a.id, 'to_user_id': self.sup_c.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_supervisor_creates_worker_in_own_team_only(self):
        self.client.force_authenticate(self.sup_a)
        res = self.client.post('/api/user/users/create-production-user/', {
            'first_name': 'Jan', 'last_name': 'Nowak', 'team_id': self.team_a.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created = CustomUser.objects.get(username=res.json()['generated_username'])
        self.assertTrue(self.team_a.members.filter(id=created.id).exists())

        res = self.client.post('/api/user/users/create-production-user/', {
            'first_name': 'Ewa', 'last_name': 'Kowal', 'team_id': self.team_b.id,
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class QualityWorkerReportTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        quality_group, _ = Group.objects.get_or_create(name='ur_quality')

        self.quality = CustomUser.objects.create_user(username='quality', password='x', is_active=True)
        self.quality.groups.add(quality_group)

        self.workshop = Workshop.objects.create(name='Warsztat Q')
        self.dept_in = Department.objects.create(name='Montaż')
        self.dept_out = Department.objects.create(name='Lakiernia')
        CurrentDepartment.objects.create(user=self.quality, department=self.dept_in)

        self.machine_in = Machine.objects.create(name='Prasa M1', workshop=self.workshop, department=self.dept_in)
        self.machine_out = Machine.objects.create(name='Kabina L1', workshop=self.workshop, department=self.dept_out)
        self.breakdown_in = Breakdown.objects.create(machine=self.machine_in, description='Awaria w Montażu')
        self.breakdown_out = Breakdown.objects.create(machine=self.machine_out, description='Awaria w Lakierni')

        self.responsible = ResponsibleForBreakdown.objects.create(workshop=self.workshop, name='Mechanik')

    def _report_ids(self):
        res = self.client.get('/api/ur/all-breakdowns-to-report/?no_pagination=true')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return {b['id'] for b in res.json()}

    def test_quality_sees_breakdowns_from_current_departments_only(self):
        self.client.force_authenticate(self.quality)
        self.assertEqual(self._report_ids(), {self.breakdown_in.id})

    def test_quality_without_departments_sees_nothing(self):
        CurrentDepartment.objects.filter(user=self.quality).delete()
        self.client.force_authenticate(self.quality)
        self.assertEqual(self._report_ids(), set())

    def test_quality_report_options_follow_departments(self):
        self.client.force_authenticate(self.quality)
        res = self.client.get('/api/ur/breakdown/raport/options/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        self.assertEqual([m['id'] for m in data['machines']], [self.machine_in.id])
        self.assertEqual([r['name'] for r in data['responsibles']], ['Mechanik'])

    def test_user_without_quality_role_and_workshop_gets_no_report(self):
        other = CustomUser.objects.create_user(username='other', password='x', is_active=True)
        CurrentDepartment.objects.create(user=other, department=self.dept_in)
        self.client.force_authenticate(other)
        self.assertEqual(self._report_ids(), set())
        res = self.client.get('/api/ur/breakdown/raport/options/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
