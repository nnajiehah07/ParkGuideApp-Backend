from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("monitoring", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="monitorsession",
            name="source_mode",
            field=models.CharField(
                choices=[("phone", "Phone camera"), ("esp32", "ESP32 camera")],
                default="esp32",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="monitorsession",
            name="camera_source",
            field=models.CharField(blank=True, default="RE-CAM-01", max_length=120),
        ),
        migrations.AlterField(
            model_name="violationalert",
            name="source_mode",
            field=models.CharField(
                choices=[("phone", "Phone camera"), ("esp32", "ESP32 camera")],
                default="esp32",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="violationalert",
            name="camera_source",
            field=models.CharField(blank=True, default="RE-CAM-01", max_length=120),
        ),
    ]
