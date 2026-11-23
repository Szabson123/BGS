from datetime import timedelta

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from user.models import CustomUser
from .models import CycleChannel, CycleStation, CycleStationCommand, CycleStationEvent, Machine

STATIONS = '/api/ur/cycle-guard/stations/'
AGENT = '/api/ur/cycle-guard/agent/'
ECLIPSE = '/api/ur/cycle-guard/eclipse/'
Event = CycleStationEvent.Kind


def channel(code, watch, dest, max_cycles=3, **extra):
    return {'boxing_code': code, 'watch_dir': watch, 'dest_dir': dest, 'max_cycles': max_cycles, **extra}


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
            'name': 'SMT-1', 'channels': [channel('nexymlitejaf27 ', r'C:\in', r'C:\out', 3),
                                          channel('NEXYMLITEJAF28', r'C:\in2', r'C:\out2', 5)],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.station = CycleStation.objects.get(pk=response.data['id'])
        self.ch1, self.ch2 = self.station.channels.order_by('id')
        self.api_key = response.data['api_key']
        self.agent = APIClient()
        self.agent.credentials(HTTP_X_API_KEY=self.api_key)

    def report(self, counter, counter2=0, alive=True, paused=False, hostname='PC-SMT-07', ip='10.1.4.23'):
        return self.agent.post(AGENT + 'status/', {
            'channels': [{'id': self.ch1.id, 'counter': counter}, {'id': self.ch2.id, 'counter': counter2}],
            'worker_alive': alive, 'worker_paused': paused, 'hostname': hostname,
        }, format='json', REMOTE_ADDR=ip)

    def detail(self):
        return self.client.get(f'{STATIONS}{self.station.id}/').data

    def channels_payload(self):
        return [{k: c[k] for k in ('id', 'name', 'boxing_code', 'watch_dir', 'dest_dir', 'max_cycles')}
                for c in self.detail()['channels']]

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
        other = self.client.post(STATIONS, {'name': 'SMT-2', 'channels': [channel('X1', 'a', 'b')],
                                            'machine': machine.id}, format='json')
        self.assertEqual(other.status_code, 400)

    def test_config_change_bumps_version_and_logs(self):
        url = f'{STATIONS}{self.station.id}/'
        channels = self.channels_payload()
        channels[0]['max_cycles'] = 69
        self.assertEqual(self.client.patch(url, {'channels': channels}, format='json').status_code, 200)
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 2)
        self.assertIn(Event.CONFIG_CHANGED, self.kinds())
        # Ta sama lista kanałów bez zmian – bez nowej wersji
        self.client.patch(url, {'channels': channels}, format='json')
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 2)
        # Zmiana samej nazwy to nie jest zmiana konfiguracji stanowiska
        self.client.patch(url, {'name': 'SMT-1A'}, format='json')
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 2)

    def test_same_watch_and_dest_dir_rejected(self):
        response = self.client.post(STATIONS, {'name': 'X', 'channels': [channel('X1', r'C:\x\\', r'c:\X')]},
                                    format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('dest_dir', response.data['channels'][0])

    def test_permissions(self):
        viewer = APIClient()
        viewer.force_authenticate(self.viewer)
        self.assertEqual(viewer.get(STATIONS).status_code, 200)
        self.assertEqual(viewer.post(f'{STATIONS}{self.station.id}/reset-counter/').status_code, 403)
        self.assertEqual(viewer.patch(f'{STATIONS}{self.station.id}/', {'settle_seconds': 30}).status_code, 403)

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
        self.assertEqual(self.detail()['pending_commands'], [{'type': 'RESET_COUNTER', 'channel': None}])

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
        self.assertEqual(data['config_version'], 1)
        self.assertEqual([c['boxing_code'] for c in data['channels']], ['NEXYMLITEJAF27', 'NEXYMLITEJAF28'])
        self.assertEqual(data['channels'][0]['watch_dir'], r'C:\in')
        self.assertEqual(data['channels'][0]['max_cycles'], 3)
        self.assertEqual(data['channels'][0]['id'], self.ch1.id)

    def test_status_logs_cycles_and_limit(self):
        self.report(1)
        self.report(1)  # ten sam licznik – bez nowego zdarzenia
        response = self.report(3)
        self.assertTrue(response.data['limit_reached'])
        self.assertEqual(self.kinds(), [Event.CYCLE, Event.CYCLE, Event.LIMIT_REACHED])
        self.assertEqual(self.station.events.filter(kind=Event.CYCLE).first().message,
                         'NEXYMLITEJAF27: cykl 3/3 (+2)')
        self.assertEqual(self.station.events.get(kind=Event.LIMIT_REACHED).channel, self.ch1)

        self.report(3)
        self.assertEqual(self.station.events.filter(kind=Event.LIMIT_REACHED).count(), 1)

        detail = self.detail()
        self.assertEqual([c['counter'] for c in detail['channels']], [3, 0])
        self.assertEqual([c['limit_reached'] for c in detail['channels']], [True, False])
        self.assertTrue(detail['limit_reached'])  # jeden kanał na limicie = stop stanowiska
        self.assertTrue(detail['online'])

    def test_channels_counted_separately(self):
        self.report(1, 4)
        self.ch1.refresh_from_db()
        self.ch2.refresh_from_db()
        self.assertEqual((self.ch1.counter, self.ch2.counter), (1, 4))
        self.assertFalse(self.detail()['limit_reached'])
        self.assertEqual(self.station.events.filter(kind=Event.CYCLE, channel=self.ch2).first().counter, 4)

    def test_unknown_channel_in_report_ignored(self):
        other = CycleStation(name='Inne')
        other.set_new_api_key()
        other.save()
        foreign = CycleChannel.objects.create(station=other, boxing_code='OBCY', watch_dir='a', dest_dir='b')
        response = self.agent.post(AGENT + 'status/', {
            'channels': [{'id': foreign.id, 'counter': 9}], 'worker_alive': True, 'worker_paused': False,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        foreign.refresh_from_db()
        self.assertEqual(foreign.counter, 0)

    def test_offline_after_silence(self):
        self.report(0)
        CycleStation.objects.filter(pk=self.station.pk).update(last_seen_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(self.client.get(f'{STATIONS}{self.station.id}/').data['online'])

    def test_reset_counter_flow(self):
        self.report(3, 2)
        self.client.post(f'{STATIONS}{self.station.id}/reset-counter/')

        commands = self.report(3, 2).data['commands']
        self.assertEqual([(c['type'], c['channel']) for c in commands], [('RESET_COUNTER', None)])

        ack = self.agent.post(f"{AGENT}commands/{commands[0]['id']}/ack/")
        self.assertEqual(ack.status_code, 200)
        self.assertIsNotNone(ack.data['acked_at'])
        self.ch1.refresh_from_db()
        self.ch2.refresh_from_db()
        self.assertEqual((self.ch1.counter, self.ch2.counter), (0, 0))  # bez kanału = wszystkie
        self.assertFalse(self.station.limit_reached)
        self.assertEqual(self.kinds()[-2:], [Event.COUNTER_RESET, Event.COUNTER_RESET])
        self.assertEqual(self.agent.get(AGENT + 'commands/').data, [])

        # Powtórne potwierdzenie nie dubluje zdarzenia
        self.agent.post(f"{AGENT}commands/{commands[0]['id']}/ack/")
        self.assertEqual(self.station.events.filter(kind=Event.COUNTER_RESET).count(), 2)

    def test_reset_single_channel(self):
        self.report(3, 2)
        url = f'{STATIONS}{self.station.id}/reset-counter/'
        response = self.client.post(url, {'channel': self.ch2.id}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['channel'], self.ch2.id)
        # Zerowanie innego kanału to osobne polecenie, nie duplikat
        self.assertEqual(self.client.post(url, {'channel': self.ch1.id}, format='json').status_code, 201)
        self.assertEqual(self.client.post(url, {'channel': self.ch2.id}, format='json').status_code, 200)

        command = self.station.commands.get(channel=self.ch2)
        self.agent.post(f'{AGENT}commands/{command.id}/ack/')
        self.ch1.refresh_from_db()
        self.ch2.refresh_from_db()
        self.assertEqual((self.ch1.counter, self.ch2.counter), (3, 0))

    def test_reset_channel_of_other_station_rejected(self):
        other = CycleStation(name='Inne')
        other.set_new_api_key()
        other.save()
        foreign = CycleChannel.objects.create(station=other, watch_dir='a', dest_dir='b')
        response = self.client.post(f'{STATIONS}{self.station.id}/reset-counter/', {'channel': foreign.id},
                                    format='json')
        self.assertEqual(response.status_code, 404)

    def test_restart_master_ack(self):
        self.client.post(f'{STATIONS}{self.station.id}/restart-master/')
        command = self.agent.get(AGENT + 'commands/').data[0]
        self.agent.post(f"{AGENT}commands/{command['id']}/ack/")
        self.assertEqual(self.kinds()[-1], Event.MASTER_RESTARTED)

    def test_cannot_ack_other_station_command(self):
        other = CycleStation(name='Inne')
        other.set_new_api_key()
        other.save()
        command = CycleStationCommand.objects.create(station=other, type=CycleStationCommand.Type.RESET_COUNTER)
        self.assertEqual(self.agent.post(f'{AGENT}commands/{command.id}/ack/').status_code, 404)

    def test_records_host_and_ip(self):
        self.report(0)
        detail = self.detail()
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


class ChannelConfigTests(CycleGuardBase):
    def url(self):
        return f'{STATIONS}{self.station.id}/'

    def test_boxing_code_normalized_and_required(self):
        self.assertEqual(self.ch1.boxing_code, 'NEXYMLITEJAF27')
        response = self.client.post(STATIONS, {'name': 'X', 'channels': [channel(' ', 'a', 'b')]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('boxing_code', response.data['channels'][0])

    def test_station_needs_a_channel(self):
        response = self.client.post(STATIONS, {'name': 'X', 'channels': []}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('channels', response.data)

    def test_boxing_code_unique_across_stations(self):
        response = self.client.post(STATIONS, {'name': 'SMT-2', 'channels': [channel('nexymlitejaf27', 'x', 'y')]},
                                    format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('SMT-1', str(response.data['channels'][0]['boxing_code']))

    def test_folder_conflicts_within_station(self):
        channels = self.channels_payload()
        channels[1]['watch_dir'] = r'c:\IN\\'  # to samo co obserwowany kanału 1
        response = self.client.patch(self.url(), {'channels': channels}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('watch_dir', response.data['channels'][1])

        channels = self.channels_payload()
        channels[1]['dest_dir'] = r'C:\in'  # docelowy jednego = obserwowany drugiego → pliki w kółko
        self.assertEqual(self.client.patch(self.url(), {'channels': channels}, format='json').status_code, 400)

        channels = self.channels_payload()
        channels[1]['dest_dir'] = r'C:\out'  # wspólny folder docelowy jest dozwolony
        self.assertEqual(self.client.patch(self.url(), {'channels': channels}, format='json').status_code, 200)

    def test_add_remove_and_swap_channels(self):
        channels = self.channels_payload()
        channels[0]['boxing_code'], channels[1]['boxing_code'] = channels[1]['boxing_code'], channels[0]['boxing_code']
        channels.append(channel('NOWY', r'C:\in3', r'C:\out3', 10))
        response = self.client.patch(self.url(), {'channels': channels}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([c['boxing_code'] for c in response.data['channels']],
                         ['NEXYMLITEJAF28', 'NEXYMLITEJAF27', 'NOWY'])

        channels = self.channels_payload()[1:]  # usunięcie pierwszego kanału
        response = self.client.patch(self.url(), {'channels': channels}, format='json')
        self.assertEqual(len(response.data['channels']), 2)
        self.assertFalse(CycleChannel.objects.filter(pk=self.ch1.pk).exists())
        self.station.refresh_from_db()
        self.assertEqual(self.station.config_version, 3)

    def test_cannot_edit_channel_of_other_station(self):
        other = CycleStation(name='Inne')
        other.set_new_api_key()
        other.save()
        foreign = CycleChannel.objects.create(station=other, boxing_code='OBCY', watch_dir='a', dest_dir='b')
        channels = self.channels_payload()
        channels[0]['id'] = foreign.id
        response = self.client.patch(self.url(), {'channels': channels}, format='json')
        self.assertEqual(response.status_code, 400)
        foreign.refresh_from_db()
        self.assertEqual(foreign.station, other)

    def test_changing_boxing_code_resets_num_qa_baseline(self):
        CycleChannel.objects.filter(pk=self.ch1.pk).update(last_num_qa=500)
        channels = self.channels_payload()
        channels[0]['max_cycles'] = 10
        self.client.patch(self.url(), {'channels': channels}, format='json')
        self.ch1.refresh_from_db()
        self.assertEqual(self.ch1.last_num_qa, 500)

        channels[0]['boxing_code'] = 'INNYKOD'
        self.client.patch(self.url(), {'channels': channels}, format='json')
        self.ch1.refresh_from_db()
        self.assertIsNone(self.ch1.last_num_qa)


@override_settings(CYCLE_GUARD_ECLIPSE_API_KEY='eclipse-secret')
class EclipseServiceTests(CycleGuardBase):
    def setUp(self):
        super().setUp()
        self.service = APIClient()
        self.service.credentials(HTTP_X_API_KEY='eclipse-secret')

    def send(self, *pairs):
        return self.service.post(ECLIPSE + 'num-qa/', {
            'results': [{'boxing_code': code, 'num_qa': num} for code, num in pairs],
        }, format='json')

    def test_requires_service_key(self):
        self.assertEqual(APIClient().get(ECLIPSE + 'channels/').status_code, 403)
        # Klucz stanowiska to nie klucz serwisu
        self.assertEqual(self.agent.get(ECLIPSE + 'channels/').status_code, 403)
        with override_settings(CYCLE_GUARD_ECLIPSE_API_KEY=''):
            empty = APIClient()
            empty.credentials(HTTP_X_API_KEY='')
            self.assertEqual(empty.get(ECLIPSE + 'channels/').status_code, 403)

    def test_channels_list_skips_inactive_and_codeless(self):
        CycleChannel.objects.create(station=self.station, watch_dir='q', dest_dir='w')  # bez kodu
        inactive = CycleStation(name='Wyłączone', is_active=False)
        inactive.set_new_api_key()
        inactive.save()
        CycleChannel.objects.create(station=inactive, boxing_code='WYL', watch_dir='a', dest_dir='b')
        data = self.service.get(ECLIPSE + 'channels/').data
        self.assertEqual([c['boxing_code'] for c in data], ['NEXYMLITEJAF27', 'NEXYMLITEJAF28'])
        self.assertEqual(data[0]['station_name'], 'SMT-1')
        self.assertIsNone(data[0]['last_num_qa'])

    def test_first_read_is_baseline_then_change_resets_channel(self):
        self.report(3, 2)
        response = self.send(('NEXYMLITEJAF27', 1000), ('NEXYMLITEJAF28', 2000), ('NIEZNANY', 1))
        self.assertEqual(response.data['results'],
                         {'NEXYMLITEJAF27': 'baseline', 'NEXYMLITEJAF28': 'baseline', 'NIEZNANY': 'unknown'})
        self.assertEqual(self.station.commands.count(), 0)

        self.assertEqual(self.send(('NEXYMLITEJAF27', 1000)).data['results'], {'NEXYMLITEJAF27': 'unchanged'})
        self.assertEqual(self.send(('nexymlitejaf27', 1001)).data['results'], {'nexymlitejaf27': 'reset'})

        command = self.station.commands.get()
        self.assertEqual((command.type, command.channel), (CycleStationCommand.Type.RESET_COUNTER, self.ch1))
        self.assertIsNone(command.created_by)
        event = self.station.events.get(kind=Event.QA_BATCH_CHANGED)
        self.assertIn('NumQA 1000 → 1001', event.message)
        self.assertEqual(event.channel, self.ch1)

        # Master wykonuje zerowanie tylko tego kanału
        pending = self.report(3, 2).data['commands']
        self.assertEqual([(c['type'], c['channel']) for c in pending], [('RESET_COUNTER', self.ch1.id)])
        self.agent.post(f"{AGENT}commands/{pending[0]['id']}/ack/")
        self.ch1.refresh_from_db()
        self.ch2.refresh_from_db()
        self.assertEqual((self.ch1.counter, self.ch2.counter, self.ch1.last_num_qa), (0, 2, 1001))

    def test_repeated_change_before_ack_not_duplicated(self):
        self.send(('NEXYMLITEJAF27', 1))
        self.send(('NEXYMLITEJAF27', 2))
        self.send(('NEXYMLITEJAF27', 3))
        self.assertEqual(self.station.commands.filter(acked_at__isnull=True).count(), 1)
        self.assertEqual(self.station.events.filter(kind=Event.QA_BATCH_CHANGED).count(), 2)

    def test_inactive_station_not_reset(self):
        self.send(('NEXYMLITEJAF27', 1))
        CycleStation.objects.filter(pk=self.station.pk).update(is_active=False)
        self.assertEqual(self.send(('NEXYMLITEJAF27', 2)).data['results'], {'NEXYMLITEJAF27': 'inactive'})
        self.assertEqual(self.station.commands.count(), 0)

    def test_invalid_payload(self):
        response = self.service.post(ECLIPSE + 'num-qa/', {'results': [{'boxing_code': 'X'}]}, format='json')
        self.assertEqual(response.status_code, 400)
