from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import Course
from user_progress.models import Badge
from user_progress.services import sync_all_badges_for_all_users


class Command(BaseCommand):
    help = 'Create selectable demo badges based on available training course/module data.'

    @transaction.atomic
    def handle(self, *args, **options):
        courses = Course.objects.prefetch_related('modules').all()
        if not courses.exists():
            self.stdout.write(self.style.WARNING('No courses found. Load training courses first.'))
            return

        course_badges = []
        created_count = 0
        updated_count = 0

        for course in courses:
            module_count = max(1, course.modules.count())
            course_title = course.title.get('en', f'Course {course.id}')
            badge_name = f'{course_title} Completion'
            payload = {
                'description': f'Complete all modules in {course_title}.',
                'required_completed_modules': module_count,
                'required_badges_count': 0,
                'course': course,
                'is_major_badge': False,
                'is_active': True,
                'auto_approve_when_eligible': False,
            }
            badge, created = Badge.objects.update_or_create(
                name=badge_name,
                defaults=payload,
            )
            course_badges.append(badge)
            if created:
                created_count += 1
            else:
                updated_count += 1

        course_badge_count = len(course_badges)
        half_badges = max(1, course_badge_count // 2)

        major_badges = [
            {
                'name': 'Training Starter',
                'description': 'Earn at least 1 course completion badge.',
                'required_completed_modules': 0,
                'required_badges_count': 1,
                'course': None,
                'is_major_badge': True,
                'is_active': True,
                'auto_approve_when_eligible': True,
            },
            {
                'name': 'Training Explorer',
                'description': 'Earn at least half of all course completion badges.',
                'required_completed_modules': 0,
                'required_badges_count': half_badges,
                'course': None,
                'is_major_badge': True,
                'is_active': True,
                'auto_approve_when_eligible': True,
            },
            {
                'name': 'Training Master',
                'description': 'Earn all course completion badges.',
                'required_completed_modules': 0,
                'required_badges_count': max(1, course_badge_count),
                'course': None,
                'is_major_badge': True,
                'is_active': True,
                'auto_approve_when_eligible': True,
            },
        ]

        for payload in major_badges:
            _, created = Badge.objects.update_or_create(
                name=payload['name'],
                defaults=payload,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        summary = sync_all_badges_for_all_users()

        self.stdout.write(
            self.style.SUCCESS(
                f'Demo badges ready. Created: {created_count}, Updated: {updated_count}. '
                f'User badge rows created: {summary["created"]}.'
            )
        )
