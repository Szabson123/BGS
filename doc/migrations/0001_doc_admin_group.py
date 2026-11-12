from django.db import migrations


def create_doc_admin_group(apps, schema_editor):
    """Pierwsza grupa modułu DOC – daje dostęp do BGS::DOC. Docelowe role zostaną zdefiniowane później."""
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='doc_admin')


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_doc_admin_group, migrations.RunPython.noop),
    ]
