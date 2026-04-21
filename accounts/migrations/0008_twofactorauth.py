from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_passkeycredential'),
    ]

    operations = [
        migrations.CreateModel(
            name='TwoFactorAuth',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('secret', models.CharField(blank=True, default='', max_length=64)),
                ('is_enabled', models.BooleanField(default=False)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_step', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='two_factor_auth', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Two-factor authentication',
                'verbose_name_plural': 'Two-factor authentication',
            },
        ),
    ]
