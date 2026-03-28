from django.db import migrations, models


def bootstrap_user_badges(apps, schema_editor):
    Badge = apps.get_model('user_progress', 'Badge')
    UserBadge = apps.get_model('user_progress', 'UserBadge')
    User = apps.get_model('accounts', 'CustomUser')

    for user in User.objects.all():
        for badge in Badge.objects.filter(is_active=True):
            UserBadge.objects.get_or_create(
                user=user,
                badge=badge,
                defaults={
                    'status': 'in_progress',
                    'is_awarded': False,
                },
            )

    UserBadge.objects.filter(status='rejected', is_awarded=False).update(status='in_progress')


class Migration(migrations.Migration):

    dependencies = [
        ('user_progress', '0005_alter_userbadge_is_awarded'),
    ]

    operations = [
        migrations.AddField(
            model_name='badge',
            name='is_major_badge',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='badge',
            name='required_badges_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='userbadge',
            name='status',
            field=models.CharField(
                choices=[
                    ('in_progress', 'In Progress'),
                    ('pending', 'Pending'),
                    ('granted', 'Granted'),
                    ('rejected', 'Rejected'),
                ],
                default='in_progress',
                max_length=20,
            ),
        ),
        migrations.RunPython(bootstrap_user_badges, migrations.RunPython.noop),
    ]
