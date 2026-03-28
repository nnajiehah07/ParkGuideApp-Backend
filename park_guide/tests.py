from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from courses.models import Course, CourseProgress, Module, ModuleProgress
from park_guide.admin_site import ParkGuideAdminSite


class AdminDashboardCompletionMetricsTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.admin_user = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.factory = RequestFactory()
        self.admin_site = ParkGuideAdminSite(name="test-admin")

    def test_dashboard_uses_catalog_totals_for_course_and_module_completion(self):
        learner_one = self.user_model.objects.create_user(
            username="learner-one",
            email="learner1@example.com",
            password="password123",
        )
        learner_two = self.user_model.objects.create_user(
            username="learner-two",
            email="learner2@example.com",
            password="password123",
        )

        course_one = Course.objects.create(title={"en": "Course 1"})
        course_two = Course.objects.create(title={"en": "Course 2"})

        module_one = Module.objects.create(course=course_one, title={"en": "Module 1"})
        module_two = Module.objects.create(course=course_one, title={"en": "Module 2"})
        Module.objects.create(course=course_two, title={"en": "Module 3"})

        CourseProgress.objects.create(
            user=learner_one,
            course=course_one,
            completed_modules=2,
            total_modules=2,
            progress=1,
            completed=True,
        )
        CourseProgress.objects.create(
            user=learner_two,
            course=course_one,
            completed_modules=2,
            total_modules=2,
            progress=1,
            completed=True,
        )

        ModuleProgress.objects.create(user=learner_one, module=module_one, completed=True)
        ModuleProgress.objects.create(user=learner_two, module=module_one, completed=True)
        ModuleProgress.objects.create(user=learner_one, module=module_two, completed=True)

        request = self.factory.get("/admin/")
        request.user = self.admin_user

        context = self.admin_site.each_context(request)

        course_panel = next(item for item in context["dashboard_chart_panels"] if item["title"] == "Course Completion")
        module_panel = next(item for item in context["dashboard_chart_panels"] if item["title"] == "Module Completion")
        course_card = next(item for item in context["dashboard_cards"] if item["title"] == "Course Completions")

        self.assertEqual(course_card["value"], 1)
        self.assertEqual(course_card["subtitle"], "50% completion rate")
        self.assertEqual(course_panel["subtitle"], "1 of 2 courses completed")
        self.assertEqual(course_panel["percent"], 50)
        self.assertEqual(module_panel["subtitle"], "2 of 3 modules completed")
        self.assertEqual(module_panel["percent"], 67)
