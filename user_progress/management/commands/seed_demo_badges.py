from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.signals import post_save

from courses.models import Course
from user_progress.models import Badge
from user_progress.signals import sync_badges_when_badge_changes
from user_progress.services import create_or_update_course_badge, sync_all_badges_for_all_users


class Command(BaseCommand):
    help = 'Create selectable demo badges based on available training course/module data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-sync',
            action='store_true',
            help='Do not sync badges for all users after seeding (faster).',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        # Creating/updating each Badge triggers a post_save signal that syncs badges for all users.
        # That makes seeding O(num_badges × num_users). We disable it during this command and
        # (by default) run a single sync once at the end.
        did_disconnect = post_save.disconnect(sync_badges_when_badge_changes, sender=Badge)
        no_sync = bool(options.get('no_sync'))

        courses = Course.objects.prefetch_related('modules').all()
        if not courses.exists():
            self.stdout.write(self.style.WARNING('No courses found. Load training courses first.'))
            return

        created_count = 0
        updated_count = 0

        for course in courses:
            existed = Badge.objects.filter(course=course, is_major_badge=False).exists()
            create_or_update_course_badge(course)
            if existed:
                updated_count += 1
            else:
                created_count += 1

        try:
            if no_sync:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Demo badges ready. Created: {created_count}, Updated: {updated_count}. '
                        'Skipped user badge sync (--no-sync).'
                    )
                )
                return

            summary = sync_all_badges_for_all_users()
            self.stdout.write(
                self.style.SUCCESS(
                    f'Demo badges ready. Created: {created_count}, Updated: {updated_count}. '
                    f'User badge rows created: {summary["created"]}.'
                )
            )
        finally:
            if did_disconnect:
                post_save.connect(sync_badges_when_badge_changes, sender=Badge)
