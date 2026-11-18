"""Baseline roles and permissions only (no users). Introduced: 2026-11-18; revisions: Git."""

from django.db import migrations

ROLES = (
    (
        'ict',
        'institution',
        ('identity.manage', 'organisation.manage', 'access.apply', 'audit.technical'),
    ),
    ('staff', 'self', ('request.own',)),
    ('hod', 'department', ('request.department',)),
    ('officer', 'assigned', ('matter.assigned',)),
    ('senior', 'department', ('matter.department',)),
    ('head', 'institution', ('matter.institution', 'access.decide')),
    ('management', 'institution', ('reports.aggregate',)),
    ('auditor', 'institution', ('audit.read',)),
    ('records', 'department', ('records.department',)),
)


def populate(apps, schema_editor):
    roles = apps.get_model('identity', 'Role').objects.using(schema_editor.connection.alias)
    permissions = apps.get_model('identity', 'Permission').objects.using(schema_editor.connection.alias)
    for code, scope, capabilities in ROLES:
        role, _created = roles.get_or_create(code=code, defaults={'scope': scope})
        role.permissions.set(
            [permissions.get_or_create(code=capability)[0] for capability in capabilities]
        )


class Migration(migrations.Migration):
    dependencies = [('identity', '0001_initial')]
    operations = [migrations.RunPython(populate, migrations.RunPython.noop)]
