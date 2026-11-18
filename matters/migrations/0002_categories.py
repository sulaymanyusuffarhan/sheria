"""Bilingual legal service catalogue. Introduced: 2026-11-18; revisions: Git."""

from django.db import migrations

CATEGORIES = (
    ('contract', 'Contract review', 'Mapitio ya mkataba'),
    ('opinion', 'Legal opinion', 'Maoni ya kisheria'),
    ('litigation', 'Litigation', 'Mashauri ya mahakama'),
    ('advice', 'Legal advice', 'Ushauri wa kisheria'),
    ('labour', 'Employment and labour', 'Ajira na kazi'),
    ('ip', 'Intellectual property', 'Miliki bunifu'),
    ('broadcasting', 'Broadcasting and media rights', 'Haki za utangazaji na vyombo vya habari'),
    ('copyright', 'Copyright infringement', 'Ukiukaji wa hakimiliki'),
    ('defamation', 'Defamation', 'Kashfa'),
    ('compliance', 'Regulatory compliance', 'Uzingatiaji wa kanuni'),
    ('mou', 'Memorandum of Understanding', 'Hati ya makubaliano'),
    ('other', 'Other legal matters', 'Masuala mengine ya kisheria'),
)


def populate(apps, schema_editor):
    categories = apps.get_model('matters', 'Category').objects.using(schema_editor.connection.alias)
    for code, name, local in CATEGORIES:
        categories.get_or_create(code=code, defaults={'name': name, 'name_sw': local})


class Migration(migrations.Migration):
    dependencies = [('matters', '0001_initial')]
    operations = [migrations.RunPython(populate, migrations.RunPython.noop)]
