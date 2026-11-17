import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
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
                                    {'file': self.upload(), 'approval_path': '["doc_quality"]'}, format='multipart')
        self.assertEqual(response.status_code, 400)

        self.approve_all(document['versions'][0])

        self.client.force_authenticate(self.author)
        response = self.client.post(f"/api/doc/documents/{document['id']}/versions/",
                                    {'file': self.upload('v2.xlsx'), 'change_description': 'Poprawka',
                                     'approval_path': '["doc_bhp"]'},
                                    format='multipart')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['latest_version']['version_number'], 2)
        self.assertEqual(response.json()['current_version_number'], 1)

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
