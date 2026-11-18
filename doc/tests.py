import json
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

import httpx

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Family, Operation, Phase, Factory, Department, DocumentVersion, ApprovalStep

User = get_user_model()
TEMP_MEDIA = tempfile.mkdtemp()


def make_user(username, *groups):
    user = User.objects.create_user(username=username, password='x', is_active=True)
    for name in groups:
        user.groups.add(Group.objects.get_or_create(name=name)[0])
    return user


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class DocApiTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = make_user('admin', 'doc_admin')
        self.author = make_user('author', 'doc_author')
        self.operator = make_user('operator', 'doc_operator')
        self.ur_only = make_user('urworker', 'ur_worker')
        self.quality = make_user('quality', 'doc_quality')
        self.bhp = make_user('bhp', 'doc_bhp')

        family = Family.objects.create(name='Kebaby')
        operation = Operation.objects.create(family=family, name='Nakładanie kebaba')
        self.phase = Phase.objects.create(operation=operation, name='Wydanie dla klienta')
        self.factory = Factory.objects.create(name='Zakład 1')
        self.department = Department.objects.create(name='Montaż')

    def upload(self, name='instrukcja.pdf'):
        return SimpleUploadedFile(name, b'%PDF-1.4 test', content_type='application/pdf')

    def create_document(self, user=None, path=('doc_quality', 'doc_bhp')):
        self.client.force_authenticate(user or self.author)
        return self.client.post('/api/doc/documents/', {
            'title': 'Wydawanie kebaba',
            'phase': self.phase.id,
            'factory': self.factory.id,
            'department': self.department.id,
            'file': self.upload(),
            'change_description': 'Pierwsza wersja',
            'approval_path': json.dumps(list(path)),
        }, format='multipart')

    def decide(self, user, step_id, decision='approve', comment=''):
        self.client.force_authenticate(user)
        return self.client.post(f'/api/doc/approval-steps/{step_id}/{decision}/', {'comment': comment}, format='json')

    def approve_all(self, version):
        """Zatwierdza wszystkie kroki wersji po kolei (dane z odpowiedzi API)."""
        users = {'doc_quality': self.quality, 'doc_bhp': self.bhp}
        for step in version['approval_steps']:
            self.assertEqual(self.decide(users[step['section']], step['id']).status_code, 200)

    def test_dictionaries_read_for_doc_users_write_for_admin(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get('/api/doc/families/').status_code, 200)
        self.assertEqual(self.client.post('/api/doc/families/', {'name': 'X'}).status_code, 403)

        self.client.force_authenticate(self.ur_only)
        self.assertEqual(self.client.get('/api/doc/families/').status_code, 403)

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post('/api/doc/families/', {'name': 'Pizze'}).status_code, 201)

    def test_used_dictionary_cannot_be_deleted(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f'/api/doc/families/{self.phase.operation.family_id}/')
        self.assertEqual(response.status_code, 400)

    def test_operations_filtered_by_family(self):
        other = Family.objects.create(name='Pizze')
        Operation.objects.create(family=other, name='Pieczenie')
        self.client.force_authenticate(self.operator)
        names = [o['name'] for o in self.client.get(f'/api/doc/operations/?family={other.id}').json()]
        self.assertEqual(names, ['Pieczenie'])

    def test_create_document_and_versions(self):
        response = self.create_document()
        self.assertEqual(response.status_code, 201, response.content)
        document = response.json()
        self.assertEqual(document['number'], f"INS-{document['id']:04d}")
        self.assertEqual(document['latest_version']['status'], 'PENDING')

        sections = [step['section'] for step in document['versions'][0]['approval_steps']]
        self.assertEqual(sections, ['doc_quality', 'doc_bhp'])

        # Kolejna wersja zablokowana, dopóki poprzednia czeka na akceptację
        response = self.client.post(f"/api/doc/documents/{document['id']}/versions/",
                                    {'file': self.upload(), 'approval_path': '["doc_quality"]', 'change_description': 'X'},
                                    format='multipart')
        self.assertEqual(response.status_code, 400)

        self.approve_all(document['versions'][0])

        self.client.force_authenticate(self.author)
        # Opis zmian jest obowiązkowy przy nowej wersji
        response = self.client.post(f"/api/doc/documents/{document['id']}/versions/",
                                    {'file': self.upload('v2.xlsx'), 'change_description': '  '}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('change_description', response.json())

        # Ścieżki nie można zmienić przy nowej wersji
        response = self.client.post(f"/api/doc/documents/{document['id']}/versions/",
                                    {'file': self.upload('v2.xlsx'), 'approval_path': '["doc_bhp"]',
                                     'change_description': 'Poprawka'},
                                    format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('nie można zmienić', response.json()['approval_path'][0])

        # Bez ścieżki w żądaniu – przechodzi z poprzedniej wersji
        response = self.client.post(f"/api/doc/documents/{document['id']}/versions/",
                                    {'file': self.upload('v2.xlsx'), 'change_description': 'Poprawka'},
                                    format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body['latest_version']['version_number'], 2)
        self.assertEqual(body['current_version_number'], 1)
        self.assertEqual(body['approval_path'], ['doc_quality', 'doc_bhp'])
        self.assertEqual([s['section'] for s in body['latest_version']['approval_steps']], ['doc_quality', 'doc_bhp'])

    def post_with_place(self, place, user=None):
        self.client.force_authenticate(user or self.author)
        return self.client.post('/api/doc/documents/', {
            'title': 'Nowe miejsce', 'factory': self.factory.id, 'department': self.department.id,
            'file': self.upload(), 'approval_path': json.dumps(['doc_quality']), **place,
        }, format='multipart')

    def test_create_document_creates_missing_family_operation_phase(self):
        response = self.post_with_place({'family_name': ' Pizze ', 'operation_name': 'Pieczenie', 'phase_name': 'Wyjęcie'})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual([body['family']['name'], body['operation']['name'], body['phase']['name']],
                         ['Pizze', 'Pieczenie', 'Wyjęcie'])

        # Istniejąca rodzina (id) + nowa operacja i faza
        family = self.phase.operation.family
        response = self.post_with_place({'family': family.id, 'operation_name': 'Pakowanie', 'phase_name': 'Folia'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Operation.objects.filter(family=family).count(), 2)

    def test_typed_names_reuse_existing_entries_case_insensitive(self):
        response = self.post_with_place({'family_name': 'kebaby', 'operation_name': 'NAKŁADANIE KEBABA',
                                         'phase_name': 'wydanie dla klienta'})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['phase']['id'], self.phase.id)
        self.assertEqual((Family.objects.count(), Operation.objects.count(), Phase.objects.count()), (1, 1, 1))

    def test_missing_place_or_failed_document_creates_nothing(self):
        response = self.post_with_place({'family_name': 'Pizze', 'operation_name': 'Pieczenie'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('phase_name', response.json())

        # Błędna ścieżka akceptacji – nowe pozycje nie mogą zostać w słownikach
        self.client.force_authenticate(self.author)
        response = self.client.post('/api/doc/documents/', {
            'title': 'X', 'factory': self.factory.id, 'department': self.department.id, 'file': self.upload(),
            'approval_path': '[]', 'family_name': 'Pizze', 'operation_name': 'Pieczenie', 'phase_name': 'Wyjęcie',
        }, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Family.objects.filter(name='Pizze').exists())

    def test_inactive_typed_name_is_rejected(self):
        Family.objects.create(name='Stare', is_active=False)
        response = self.post_with_place({'family_name': 'stare', 'operation_name': 'A', 'phase_name': 'B'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('family_name', response.json())

    def test_legacy_document_without_path_gets_path_once(self):
        """Dokument sprzed ścieżek: przy nowej wersji ścieżkę trzeba wybrać, potem jest już stała."""
        document = self.create_document().json()
        ApprovalStep.objects.filter(version_id=document['versions'][0]['id']).delete()
        DocumentVersion.objects.filter(pk=document['versions'][0]['id']).update(status='APPROVED')

        self.client.force_authenticate(self.author)
        url = f"/api/doc/documents/{document['id']}/versions/"
        response = self.client.post(url, {'file': self.upload(), 'change_description': 'Poprawka'}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('approval_path', response.json())
        response = self.client.post(url, {'file': self.upload(), 'approval_path': '["doc_bhp"]',
                                          'change_description': 'Poprawka'}, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['approval_path'], ['doc_bhp'])

    def test_author_sees_only_own_documents_admin_sees_all(self):
        self.create_document()
        other_author = make_user('author2', 'doc_author')
        self.client.force_authenticate(other_author)
        self.assertEqual(self.client.get('/api/doc/documents/').json(), [])
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(self.client.get('/api/doc/documents/').json()), 1)

    def test_operator_cannot_add_documents(self):
        self.assertEqual(self.create_document(self.operator).status_code, 403)

    def test_operator_sees_only_latest_approved_version(self):
        document = self.create_document().json()
        v1 = document['versions'][0]['id']

        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(f'/api/doc/operator/documents/?phase={self.phase.id}').json(), [])
        self.assertEqual(self.client.get(f'/api/doc/versions/{v1}/file/').status_code, 404)

        DocumentVersion.objects.filter(pk=v1).update(status='APPROVED')
        v2 = DocumentVersion.objects.create(
            document_id=document['id'], version_number=2, file=self.upload(), original_filename='v2.pdf',
            uploaded_by=self.author,
        )
        docs = self.client.get(f'/api/doc/operator/documents/?phase={self.phase.id}').json()
        self.assertEqual(docs[0]['current_version']['id'], v1)

        DocumentVersion.objects.filter(pk=v2.pk).update(status='APPROVED')
        docs = self.client.get(f'/api/doc/operator/documents/?phase={self.phase.id}').json()
        self.assertEqual(docs[0]['current_version']['id'], v2.pk)

        response = self.client.get(f'/api/doc/versions/{v2.pk}/file/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')

    def test_approval_path_validation(self):
        self.assertEqual(self.create_document(path=()).status_code, 400)
        self.assertEqual(self.create_document(path=('doc_quality', 'doc_quality')).status_code, 400)
        self.assertEqual(self.create_document(path=('doc_unknown',)).status_code, 400)
        # Sekcja Production nie ma nikogo – nikt nie mógłby zaakceptować
        response = self.create_document(path=('doc_production',))
        self.assertEqual(response.status_code, 400)
        self.assertIn('Production', response.json()['approval_path'][0])

    def test_steps_are_approved_in_order_by_section_members(self):
        version = self.create_document().json()['versions'][0]
        quality_step, bhp_step = version['approval_steps']
        self.assertTrue(quality_step['is_active'])
        self.assertFalse(bhp_step['is_active'])

        # BHP widzi pustą skrzynkę i nie może jeszcze decydować; Quality widzi swój krok
        self.client.force_authenticate(self.bhp)
        self.assertEqual(self.client.get('/api/doc/approvals/').json(), [])
        self.assertEqual(self.decide(self.bhp, bhp_step['id']).status_code, 400)
        self.assertEqual(self.decide(self.bhp, quality_step['id']).status_code, 403)

        self.client.force_authenticate(self.quality)
        inbox = self.client.get('/api/doc/approvals/').json()
        self.assertEqual([item['id'] for item in inbox], [quality_step['id']])
        # Akceptujący może otworzyć plik oczekującej wersji
        self.assertEqual(self.client.get(f"/api/doc/versions/{version['id']}/file/").status_code, 200)

        self.assertEqual(self.decide(self.quality, quality_step['id']).status_code, 200)
        self.assertEqual(self.decide(self.quality, quality_step['id']).status_code, 400)  # już rozpatrzony
        self.assertEqual(DocumentVersion.objects.get(pk=version['id']).status, 'PENDING')

        self.client.force_authenticate(self.bhp)
        self.assertEqual(len(self.client.get('/api/doc/approvals/').json()), 1)
        self.assertEqual(self.decide(self.bhp, bhp_step['id']).status_code, 200)
        self.assertEqual(DocumentVersion.objects.get(pk=version['id']).status, 'APPROVED')

    def test_rejection_requires_comment_and_ends_version(self):
        version = self.create_document().json()['versions'][0]
        quality_step = version['approval_steps'][0]
        self.assertEqual(self.decide(self.quality, quality_step['id'], 'reject').status_code, 400)
        self.assertEqual(self.decide(self.quality, quality_step['id'], 'reject', 'Zła kolejność').status_code, 200)

        rejected = DocumentVersion.objects.get(pk=version['id'])
        self.assertEqual(rejected.status, 'REJECTED')
        self.assertEqual(rejected.decision_comment, 'Zła kolejność')
        self.client.force_authenticate(self.bhp)
        self.assertEqual(self.client.get('/api/doc/approvals/').json(), [])

    def test_rejection_note_with_screenshots(self):
        document = self.create_document().json()
        quality_step = document['versions'][0]['approval_steps'][0]

        self.client.force_authenticate(self.quality)
        response = self.client.post(f"/api/doc/approval-steps/{quality_step['id']}/reject/", {
            'comment': 'Złe zdjęcie w kroku 2',
            'files': [
                SimpleUploadedFile('zrzut-1.png', b'\x89PNG fake', content_type='image/png'),
                SimpleUploadedFile('uwagi.txt', b'tekst', content_type='text/plain'),
            ],
        }, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)

        # Notatka jest widoczna w szczegółach dokumentu dodającego
        self.client.force_authenticate(self.author)
        detail = self.client.get(f"/api/doc/documents/{document['id']}/").json()
        step = detail['versions'][0]['approval_steps'][0]
        self.assertEqual(step['comment'], 'Złe zdjęcie w kroku 2')
        self.assertEqual([(a['original_filename'], a['is_image']) for a in step['attachments']],
                         [('zrzut-1.png', True), ('uwagi.txt', False)])
        self.assertEqual(self.client.get(step['attachments'][0]['url']).status_code, 200)

        # Operator nie ma dostępu do notatek odrzuconej wersji
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(step['attachments'][0]['url']).status_code, 404)

    def test_rejection_rejects_too_many_files(self):
        quality_step = self.create_document().json()['versions'][0]['approval_steps'][0]
        self.client.force_authenticate(self.quality)
        files = [SimpleUploadedFile(f'z{i}.png', b'x', content_type='image/png') for i in range(11)]
        response = self.client.post(f"/api/doc/approval-steps/{quality_step['id']}/reject/",
                                    {'comment': 'Za dużo', 'files': files}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ApprovalStep.objects.get(pk=quality_step['id']).status, 'PENDING')

    def test_admin_creates_user_and_sets_groups_without_touching_ur_groups(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/doc/admin/users/', {
            'first_name': 'Anna', 'last_name': 'Nowak', 'roles': ['doc_author'], 'sections': ['doc_quality'],
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        created = response.json()
        self.assertEqual(created['username'], 'anwak')
        self.assertEqual(created['roles'], ['doc_author'])
        self.assertFalse(created['is_active'])

        response = self.client.patch(f'/api/doc/admin/users/{self.ur_only.id}/',
                                     {'roles': ['doc_operator'], 'sections': ['doc_bhp']}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        groups = set(self.ur_only.groups.values_list('name', flat=True))
        self.assertEqual(groups, {'ur_worker', 'doc_operator', 'doc_bhp'})

        self.client.force_authenticate(self.author)
        self.assertEqual(self.client.get('/api/doc/admin/users/').status_code, 403)


@override_settings(MEDIA_ROOT=TEMP_MEDIA, DOC_CONVERTER_URL='http://converter', DOC_CONVERTER_API_KEY='secret',
                   DOC_CONVERTER_CALLBACK_BASE='http://django')
class DocConversionTests(APITestCase):
    """Arkusze idą do BGS-converter (fire & forget), wynik wraca callbackiem."""

    def setUp(self):
        self.author = make_user('author', 'doc_author')
        make_user('quality', 'doc_quality')
        family = Family.objects.create(name='Kebaby')
        operation = Operation.objects.create(family=family, name='Nakładanie')
        self.phase = Phase.objects.create(operation=operation, name='Wydanie')
        self.factory = Factory.objects.create(name='Zakład 1')
        self.department = Department.objects.create(name='Montaż')
        # Wątek w tle wykonujemy od razu, w tym samym wątku testu
        patcher = mock.patch('doc.conversion.start_background', lambda target, *args: target(*args))
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self, filename='instrukcja.xlsx', converter_response=None):
        post = mock.Mock(return_value=converter_response or httpx.Response(202, json={'status': 'accepted'}))
        if isinstance(converter_response, Exception):
            post = mock.Mock(side_effect=converter_response)
        self.client.force_authenticate(self.author)
        with mock.patch('doc.conversion.httpx.post', post), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post('/api/doc/documents/', {
                'title': 'T', 'phase': self.phase.id, 'factory': self.factory.id, 'department': self.department.id,
                'file': SimpleUploadedFile(filename, b'xlsx-bytes'), 'approval_path': '["doc_quality"]',
            }, format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        return DocumentVersion.objects.get(document_id=response.json()['id']), post

    def callback(self, version, key='secret', **data):
        self.client.force_authenticate(None)
        payload = {'job_id': version.conversion_job_id, 'status': 'done', **data}
        return self.client.post(f'/api/doc/versions/{version.id}/conversion-callback/', payload,
                                format='multipart', HTTP_X_API_KEY=key)

    def test_spreadsheet_is_sent_and_pdf_callback_saved(self):
        version, post = self.create()
        self.assertEqual(version.conversion_status, 'PENDING')
        url = post.call_args.args[0]
        sent = post.call_args.kwargs
        self.assertEqual(url, 'http://converter/convert')
        self.assertEqual(sent['headers'], {'X-Api-Key': 'secret'})
        self.assertEqual(sent['data']['callback_url'], f'http://django/api/doc/versions/{version.id}/conversion-callback/')

        response = self.callback(version, pdf=SimpleUploadedFile('document.pdf', b'%PDF-1.4 ok'))
        self.assertEqual(response.status_code, 204, response.content)
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'DONE')

        self.client.force_authenticate(self.author)
        detail = self.client.get(f'/api/doc/documents/{version.document_id}/').json()['versions'][0]
        self.assertEqual(detail['pdf_url'], f'/api/doc/versions/{version.id}/file/?variant=pdf')
        pdf = self.client.get(detail['pdf_url'])
        self.assertEqual(b''.join(pdf.streaming_content), b'%PDF-1.4 ok')
        self.assertIn('instrukcja.pdf', pdf.headers['Content-Disposition'])

    def test_pdf_upload_is_not_converted(self):
        version, post = self.create('instrukcja.pdf')
        self.assertEqual(version.conversion_status, 'NOT_NEEDED')
        post.assert_not_called()

    def test_failed_conversion_is_saved_and_can_be_retried(self):
        version, _ = self.create()
        response = self.callback(version, status='failed', error='Przekroczono limit czasu konwersji (180 s).')
        self.assertEqual(response.status_code, 204)
        version.refresh_from_db()
        self.assertEqual((version.conversion_status, version.conversion_error),
                         ('FAILED', 'Przekroczono limit czasu konwersji (180 s).'))

        old_job = version.conversion_job_id
        self.client.force_authenticate(self.author)
        post = mock.Mock(return_value=httpx.Response(202))
        with mock.patch('doc.conversion.httpx.post', post), self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(f'/api/doc/versions/{version.id}/convert/').status_code, 202)
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'PENDING')
        self.assertNotEqual(version.conversion_job_id, old_job)
        post.assert_called_once()

        # Callback starego zlecenia jest odrzucany
        stale = self.callback(version, pdf=SimpleUploadedFile('d.pdf', b'%PDF'), job_id=old_job)
        self.assertEqual(stale.status_code, 409)

    def test_converter_down_marks_failed(self):
        version, _ = self.create(converter_response=httpx.ConnectError('connection refused'))
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'FAILED')
        self.assertIn('niedostępny', version.conversion_error)

    def test_converter_rejection_marks_failed(self):
        version, _ = self.create(converter_response=httpx.Response(400, json={'detail': 'Nieobsługiwany typ pliku'}))
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'FAILED')
        self.assertIn('Nieobsługiwany typ pliku', version.conversion_error)

    @override_settings(DOC_CONVERTER_URL='')
    def test_missing_configuration_marks_failed(self):
        version, post = self.create()
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'FAILED')
        post.assert_not_called()

    def test_callback_requires_key(self):
        version, _ = self.create()
        self.assertEqual(self.callback(version, key='zly', status='failed').status_code, 401)
        version.refresh_from_db()
        self.assertEqual(version.conversion_status, 'PENDING')

    def test_retry_only_after_failure_or_when_stale(self):
        version, _ = self.create()
        self.client.force_authenticate(self.author)
        url = f'/api/doc/versions/{version.id}/convert/'
        self.assertEqual(self.client.post(url).status_code, 400)  # świeżo „w toku”

        DocumentVersion.objects.filter(pk=version.pk).update(
            conversion_requested_at=timezone.now() - timedelta(minutes=30))
        with mock.patch('doc.conversion.httpx.post', mock.Mock(return_value=httpx.Response(202))), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(url).status_code, 202)

