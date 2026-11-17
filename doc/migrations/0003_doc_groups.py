from django.db import migrations

DOC_GROUPS = ['doc_admin', 'doc_author', 'doc_operator', 'doc_quality', 'doc_bhp', 'doc_production']


def create_doc_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in DOC_GROUPS:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ('doc', '0002_documents'),
    ]

    operations = [
        migrations.RunPython(create_doc_groups, migrations.RunPython.noop),
    ]
