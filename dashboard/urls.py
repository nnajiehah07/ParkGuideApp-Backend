from django.urls import path
from django.views.generic import RedirectView
from django.contrib.auth.views import LoginView, LogoutView
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index_redirect, name='index'),
    path('login/', LoginView.as_view(template_name='dashboard/login.html', next_page='dashboard:home'), name='login'),
    path('logout/', LogoutView.as_view(next_page='dashboard:login'), name='logout'),
    path('dashboard/sso/', views.dashboard_sso_login, name='sso_login'),
    path('dashboard/', views.dashboard_home, name='home'),
    path('dashboard/users/', views.dashboard_users, name='users'),
    path('dashboard/requests/', views.dashboard_requests, name='requests'),
    path('dashboard/requests/<int:application_id>/cv/', views.dashboard_request_cv, name='request_cv'),
    path('dashboard/courses/', views.dashboard_courses, name='courses'),
    path('dashboard/enrollments/', views.dashboard_enrollments, name='enrollments'),
    path('dashboard/courses/<int:course_id>/', views.dashboard_course_details, name='course_details'),
    path('dashboard/courses/create/', views.dashboard_course_create, name='course_create'),
    path('dashboard/courses/<int:course_id>/edit/', views.dashboard_course_edit, name='course_edit'),
    path('dashboard/courses/<int:course_id>/delete/', views.dashboard_course_delete, name='course_delete'),
    path('dashboard/courses/import/', views.dashboard_course_import, name='course_import'),
    
    # API endpoints for inline editing
    path('api/courses/<int:course_id>/chapters/', views.api_chapter_save, name='api_chapter_create'),
    path('api/chapters/<int:chapter_id>/', views.api_chapter_save, name='api_chapter_update'),
    path('api/chapters/<int:chapter_id>/delete/', views.api_chapter_delete, name='api_chapter_delete'),
    
    path('api/chapters/<int:chapter_id>/lessons/', views.api_lesson_save, name='api_lesson_create'),
    path('api/lessons/<int:lesson_id>/', views.api_lesson_save, name='api_lesson_update'),
    path('api/lessons/<int:lesson_id>/delete/', views.api_lesson_delete, name='api_lesson_delete'),
    
    path('api/chapters/<int:chapter_id>/quizzes/', views.api_quiz_save, name='api_quiz_create'),
    path('api/quizzes/<int:quiz_id>/', views.api_quiz_save, name='api_quiz_update'),
    path('api/quizzes/<int:quiz_id>/delete/', views.api_quiz_delete, name='api_quiz_delete'),
    
    path('api/chapters/<int:chapter_id>/exercises/', views.api_exercise_save, name='api_exercise_create'),
    path('api/exercises/<int:exercise_id>/', views.api_exercise_save, name='api_exercise_update'),
    path('api/exercises/<int:exercise_id>/delete/', views.api_exercise_delete, name='api_exercise_delete'),
    
    path('dashboard/progress/', RedirectView.as_view(pattern_name='dashboard:enrollments', permanent=False), name='progress'),
    path('api/guides/<int:user_id>/progress/', views.dashboard_student_progress, name='guide_progress'),
    path('api/guides/<int:user_id>/progress/reset/', views.dashboard_reset_student_progress, name='guide_progress_reset'),
    
    # kept for compatibility
    path('api/students/<int:user_id>/progress/', views.dashboard_student_progress, name='student_progress'),
    path('api/students/<int:user_id>/progress/reset/', views.dashboard_reset_student_progress, name='student_progress_reset'),
    
    path('dashboard/badges/', views.dashboard_badges, name='badges'),
    path('dashboard/notifications/', views.dashboard_notifications, name='notifications'),
    path('notifications/feed/', views.header_notifications_feed, name='header_notifications_feed'),
    path('notifications/actions/', views.header_notifications_action, name='header_notifications_action'),
    path('dashboard/secure-files/', views.dashboard_secure_files, name='secure_files'),
    path('dashboard/backups/', views.dashboard_backups, name='backups'),
]
