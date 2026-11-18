"""Apply projection checks after the published-status backfill. Revisions: Git."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('matters', '0003_public_projection')]
    operations = [
        migrations.AddConstraint(
            model_name='legalmatter',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        'status__in',
                        [
                            'submitted',
                            'preliminary',
                            'information',
                            'accepted',
                            'assigned',
                            'review',
                            'head_review',
                            'approved',
                            'completed',
                            'rejected',
                            'closed',
                        ],
                    )
                ),
                name='matters_valid_status',
            ),
        ),
        migrations.AddConstraint(
            model_name='legalmatter',
            constraint=models.CheckConstraint(
                condition=models.Q(('classification__in', ['internal', 'confidential', 'restricted'])),
                name='matters_valid_classification',
            ),
        ),
        migrations.AddConstraint(
            model_name='legalrequest',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        'public_status__in',
                        ['awaiting', 'processing', 'information', 'completed', 'rejected', 'closed'],
                    )
                ),
                name='staff_valid_status',
            ),
        ),
        migrations.AddConstraint(
            model_name='requestupdate',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        'status__in',
                        ['awaiting', 'processing', 'information', 'completed', 'rejected', 'closed'],
                    )
                ),
                name='staff_valid_update_status',
            ),
        ),
    ]
