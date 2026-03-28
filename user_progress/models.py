from django.conf import settings
from django.db import models

from courses.models import CourseProgress, ModuleProgress


class Badge(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    course = models.ForeignKey(
        'courses.Course',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='progress_badges',
    )
    required_completed_modules = models.PositiveIntegerField(default=1)
    is_major_badge = models.BooleanField(default=False)
    required_badges_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    auto_approve_when_eligible = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('required_completed_modules', 'name')

    def __str__(self):
        if self.is_major_badge:
            return f"{self.name} (major badge: {self.required_badges_count} badge requirement)"
        if self.course_id:
            course_title = self.course.title.get('en', 'Course')
            return f"{self.name} - {course_title} (>= {self.required_completed_modules} modules)"
        return f"{self.name} (>= {self.required_completed_modules} modules)"


class UserBadge(models.Model):
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_PENDING = 'pending'
    STATUS_GRANTED = 'granted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = (
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_PENDING, 'Pending'),
        (STATUS_GRANTED, 'Granted'),
        (STATUS_REJECTED, 'Rejected'),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='badge_progress')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='user_badges')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    is_awarded = models.BooleanField(default=False)
    awarded_at = models.DateTimeField(auto_now_add=True)
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='badge_awards_made',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='badge_revocations_made',
    )

    class Meta:
        unique_together = ('user', 'badge')
        ordering = ('-awarded_at',)

    def __str__(self):
        return f"{self.user} - {self.badge.name} ({self.status})"


class ModuleProgressRecord(ModuleProgress):
    class Meta:
        proxy = True
        app_label = 'user_progress'
        verbose_name = 'Module Progress'
        verbose_name_plural = 'Module Progress'


class CourseProgressRecord(CourseProgress):
    class Meta:
        proxy = True
        app_label = 'user_progress'
        verbose_name = 'Course Progress'
        verbose_name_plural = 'Course Progress'
