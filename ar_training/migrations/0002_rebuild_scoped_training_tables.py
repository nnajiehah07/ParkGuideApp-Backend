from django.db import migrations


def create_missing_scoped_tables(apps, schema_editor):
    existing_tables = set(schema_editor.connection.introspection.table_names())

    model_specs = [
        ('ARScenario', 'ar_training_arscenario'),
        ('ARPanorama', 'ar_training_arpanorama'),
        ('ARQuizQuestion', 'ar_training_arquizquestion'),
        ('ARHotspot', 'ar_training_arhotspot'),
        ('ARTrainingProgress', 'ar_training_artrainingprogress_v2'),
    ]

    for model_name, table_name in model_specs:
        if table_name in existing_tables:
            continue

        model = apps.get_model('ar_training', model_name)
        original_db_table = model._meta.db_table
        if model_name == 'ARTrainingProgress':
            model._meta.db_table = table_name
        try:
            schema_editor.create_model(model)
        finally:
            model._meta.db_table = original_db_table


class Migration(migrations.Migration):

    dependencies = [
        ('ar_training', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(create_missing_scoped_tables, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterModelTable(
                    name='artrainingprogress',
                    table='ar_training_artrainingprogress_v2',
                ),
            ],
        ),
    ]
