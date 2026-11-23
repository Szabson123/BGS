from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import CustomUser
from .models import CycleStation, CycleStationCommand, CycleStationEvent, Machine

STATIONS = '/api/ur/cycle-guard/stations/'
AGENT = '/api/ur/cycle-guard/agent/'
Event = CycleStationEvent.Kind


def make_user(username, *groups, **extra):
    user = CustomUser.objects.create_user(username=username, password='x', is_active=True, **extra)
    for name in groups:
        user.groups.add(Group.objects.get_or_create(name=name)[0])
    return user


class CycleGuardBase(TestCase):
    def setUp(self):
        self.engineer = make_user('engineer', 'ur_process_engineer')
        self.viewer = make_user('viewer', 'ur_worker')
        self.client = APIClient()
        self.client.force_authenticate(self.engineer)
        response = self.client.post(STATIONS, {
            'name': 'SMT-1', 'watch_dir': r'C:\in', 'dest_dir': r'C:\out', 'max_cycles': 3,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.station = CycleStation.objects.get(pk=response.data['id'])
        self.api_key = response.data['api_key']
        self.agent = APIClient()
        self.agent.credentials(HTTP_X_API_KEY=self.api_key)

    def report(self, counter, alive=True, paused=False, hostname='PC-SMT-07', ip='10.1.4.23'):
        return self.agent.post(AGENT + 'status/', {
            'counter': counter, 'worker_alive': alive, 'worker_paused': paused, 'hostname': hostname,
        }, format='json', REMOTE_ADDR=ip)

    def kinds(self):
        return list(self.station.events.order_by('created_at', 'id').values_list('kind', flat=True))


class StationPanelTests(CycleGuardBase):
    def test_key_shown_only_on_create_and_stored_hashed(self):
        self.assertNotEqual(self.station.api_key_hash, self.api_key)
        detail = self.client.get(f'{STATIONS}{self.station.id}/').data
        self.assertNotIn('api_key', detail)
        self.assertNotIn('api_key_hash', detail)

    def test_machine_link_is_optional_and_one_to_one(self):
        self.assertIsNone(self.station.machine)
        machine = Machine.objects.create(name='Piec')
        url = f'{STATIONS}{self.station.id}/'
        self.assertEqual(self.client.patch(url, {'machine': machine.id}, format='json').status_code, 200)
        other = self.client.post(STATIONS, {'name': 'SMT-2', 'watch_dir': 'a', 'dest_dir': 'b',
                                            'machine': machine.id}, format='json')
        self.assertEqual(other.status_code, 400)

    def test_config_change_bumps_version_and_logs(self):
        url = f'{STATIONS}{self.station.id}/'
        self.client.patch(url, {'max_cycles': 69}, format='json')
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 2)
        self.assertIn(Event.CONFIG_CHANGED, self.kinds())
        # Zmiana samej nazwy to nie jest zmiana konfiguracji stanowiska
        self.client.patch(url, {'name': 'SMT-1A'}, format='json')
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 2)

    def test_same_watch_and_dest_dir_rejected(self):
        response = self.client.post(STATIONS, {'name': 'X', 'watch_dir': r'C:\in\\', 'dest_dir': r'c:\IN'},
                                    format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('dest_dir', response.data)

    def test_permissions(self):
        viewer = APIClient()
        viewer.force_authenticate(self.viewer)
        self.assertEqual(viewer.get(STATIONS).status_code, 200)
        self.assertEqual(viewer.post(f'{STATIONS}{self.station.id}/reset-counter/').status_code, 403)
        self.assertEqual(viewer.patch(f'{STATIONS}{self.station.id}/', {'max_cycles': 5}).status_code, 403)

        outsider = APIClient()
        outsider.force_authenticate(make_user('doc_only', 'doc_operator'))
        self.assertEqual(outsider.get(STATIONS).status_code, 403)

        admin = APIClient()
        admin.force_authenticate(make_user('uradmin', 'ur_admin'))
        self.assertEqual(admin.post(f'{STATIONS}{self.station.id}/restart-master/').status_code, 201)

    def test_command_not_duplicated_while_pending(self):
        url = f'{STATIONS}{self.station.id}/reset-counter/'
        self.assertEqual(self.client.post(url).status_code, 201)
        self.assertEqual(self.client.post(url).status_code, 200)
        self.assertEqual(self.station.commands.count(), 1)
        self.assertEqual(self.client.get(f'{STATIONS}{self.station.id}/').data['pending_commands'],
                         ['RESET_COUNTER'])

    def test_regenerate_key_invalidates_old(self):
        new_key = self.client.post(f'{STATIONS}{self.station.id}/regenerate-key/').data['api_key']
        self.assertEqual(self.agent.get(AGENT + 'config/').status_code, 401)
        self.agent.credentials(HTTP_X_API_KEY=new_key)
        self.assertEqual(self.agent.get(AGENT + 'config/').status_code, 200)


class AgentTests(CycleGuardBase):
    def test_health_is_public(self):
        self.assertEqual(APIClient().get(AGENT + 'health/').data['status'], 'ok')

    def test_agent_requires_valid_key(self):
        self.assertEqual(APIClient().get(AGENT + 'config/').status_code, 401)
        bad = APIClient()
        bad.credentials(HTTP_X_API_KEY='zly-klucz')
        self.assertEqual(bad.get(AGENT + 'config/').status_code, 401)

    def test_config(self):
        data = self.agent.get(AGENT + 'config/').data
        self.assertEqual(data['max_cycles'], 3)
        self.assertEqual(data['watch_dir'], r'C:\in')
        self.assertEqual(data['config_version'], 1)

    def test_status_logs_cycles_and_limit(self):
        self.report(1)
        self.report(1)  # ten sam licznik – bez nowego zdarzenia
        response = self.report(3)
        self.assertTrue(response.data['limit_reached'])
        self.assertEqual(self.kinds(), [Event.CYCLE, Event.CYCLE, Event.LIMIT_REACHED])
        self.assertEqual(self.station.events.filter(kind=Event.CYCLE).first().message, 'Cykl 3/3 (+2)')

        self.report(3)
        self.assertEqual(self.station.events.filter(kind=Event.LIMIT_REACHED).count(), 1)

        detail = self.client.get(f'{STATIONS}{self.station.id}/').data
        self.assertEqual(detail['counter'], 3)
        self.assertTrue(detail['limit_reached'])
        self.assertTrue(detail['online'])

    def test_offline_after_silence(self):
        self.report(0)
        CycleStation.objects.filter(pk=self.station.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(self.client.get(f'{STATIONS}{self.station.id}/').data['online'])

    def test_reset_counter_flow(self):
        self.report(3)
        self.client.post(f'{STATIONS}{self.station.id}/reset-counter/')

        commands = self.report(3).data['commands']
        self.assertEqual([c['type'] for c in commands], ['RESET_COUNTER'])

        ack = self.agent.post(f"{AGENT}commands/{commands[0]['id']}/ack/")
        self.assertEqual(ack.status_code, 200)
        self.assertIsNotNone(ack.data['acked_at'])
        self.station.refresh_from_db()
        self.assertEqual(self.station.counter, 0)
        self.assertFalse(self.station.limit_reached)
        self.assertEqual(self.kinds()[-1], Event.COUNTER_RESET)
        self.assertEqual(self.agent.get(AGENT + 'commands/').data, [])

        # Powtórne potwierdzenie nie dubluje zdarzenia
        self.agent.post(f"{AGENT}commands/{commands[0]['id']}/ack/")
        self.assertEqual(self.station.events.filter(kind=Event.COUNTER_RESET).count(), 1)

    def test_restart_master_ack(self):
        self.client.post(f'{STATIONS}{self.station.id}/restart-master/')
        command = self.agent.get(AGENT + 'commands/').data[0]
        self.agent.post(f"{AGENT}commands/{command['id']}/ack/")
        self.assertEqual(self.kinds()[-1], Event.MASTER_RESTARTED)

    def test_cannot_ack_other_station_command(self):
        other = CycleStation(name='Inne', watch_dir='a', dest_dir='b')
        other.set_new_api_key()
        other.save()
        command = CycleStationCommand.objects.create(station=other, type=CycleStationCommand.Type.RESET_COUNTER)
        self.assertEqual(self.agent.post(f'{AGENT}commands/{command.id}/ack/').status_code, 404)

    def test_records_host_and_ip(self):
        self.report(0)
        detail = self.client.get(f'{STATIONS}{self.station.id}/').data
        self.assertEqual(detail['last_hostname'], 'PC-SMT-07')
        self.assertEqual(detail['last_ip'], '10.1.4.23')
        self.assertEqual(detail['active_threats'], 0)

    def test_ip_change_on_same_computer_is_not_a_threat(self):
        self.report(0)
        self.report(0, ip='10.1.4.99')  # DHCP
        self.assertEqual(self.station.events.filter(kind__in=CycleStationEvent.THREAT_KINDS).count(), 0)
        self.station.refresh_from_db()
        self.assertEqual(self.station.last_ip, '10.1.4.99')

    def test_key_used_on_two_computers(self):
        self.report(0)
        self.report(0, hostname='PC-BIURO', ip='10.1.9.5')
        self.report(0)  # komputery zgłaszają się na zmianę
        self.report(0, hostname='PC-BIURO', ip='10.1.9.5')

        conflicts = self.station.events.filter(kind=Event.KEY_CONFLICT)
        self.assertEqual(conflicts.count(), 1)  # bez zalewania ostrzeżeniami
        self.assertIn('PC-BIURO (10.1.9.5)', conflicts.first().message)
        self.assertIn('PC-SMT-07 (10.1.4.23)', conflicts.first().message)
        self.assertEqual(self.client.get(f'{STATIONS}{self.station.id}/').data['active_threats'], 1)

    def test_computer_replaced_after_silence(self):
        self.report(0)
        CycleStation.objects.filter(pk=self.station.pk).update(last_seen_at=timezone.now() - timedelta(hours=2))
        self.report(0, hostname='PC-SMT-NOWY', ip='10.1.4.50')
        self.assertEqual(self.kinds(), [Event.HOST_CHANGED])
        self.assertIn('PC-SMT-NOWY', self.station.events.first().message)

    def test_threats_list_and_acknowledge(self):
        self.report(0)
        self.report(0, hostname='PC-BIURO', ip='10.1.9.5')
        self.agent.post(AGENT + 'events/', {'kind': 'TESTER_CLOSED'}, format='json')  # nie jest zagrożeniem

        url = f'{STATIONS}{self.station.id}/threats/'
        threats = self.client.get(url).data
        self.assertEqual([t['kind'] for t in threats], ['KEY_CONFLICT'])
        self.assertTrue(threats[0]['is_threat'])

        viewer = APIClient()
        viewer.force_authenticate(self.viewer)
        self.assertEqual(viewer.get(url).status_code, 200)
        self.assertEqual(viewer.post(f"{url}{threats[0]['id']}/acknowledge/").status_code, 403)

        ack = self.client.post(f"{url}{threats[0]['id']}/acknowledge/")
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.data['acknowledged_by_name'], 'engineer')
        self.assertEqual(self.client.get(url).data, [])
        self.assertEqual(len(self.client.get(url + '?all=1').data), 1)
        self.assertEqual(self.client.get(f'{STATIONS}{self.station.id}/').data['active_threats'], 0)

        # Zwykłego zdarzenia nie da się "potwierdzić" jako zagrożenia
        cycle = self.station.events.get(kind=Event.TESTER_CLOSED)
        self.assertEqual(self.client.post(f'{url}{cycle.id}/acknowledge/').status_code, 404)

    def test_agent_events(self):
        response = self.agent.post(AGENT + 'events/', {'kind': 'BACKEND_UNAVAILABLE', 'message': '2 min'},
                                   format='json')
        self.assertEqual(response.status_code, 201)
        # Agent nie może sam wpisać np. zerowania licznika
        self.assertEqual(self.agent.post(AGENT + 'events/', {'kind': 'COUNTER_RESET'}, format='json').status_code, 400)

        events = self.client.get(f'{STATIONS}{self.station.id}/events/?limit=1').data
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['kind'], 'BACKEND_UNAVAILABLE')
