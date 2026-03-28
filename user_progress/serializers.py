from rest_framework import serializers

from .models import Badge, UserBadge


class BadgeStatusSerializer(serializers.ModelSerializer):
    course_id = serializers.IntegerField(source='course.id', read_only=True)
    course_title = serializers.SerializerMethodField()
    earned = serializers.SerializerMethodField()
    pending = serializers.SerializerMethodField()
    rejected = serializers.SerializerMethodField()
    in_progress = serializers.SerializerMethodField()
    eligible = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    completed_modules = serializers.SerializerMethodField()
    completed_badges = serializers.SerializerMethodField()

    class Meta:
        model = Badge
        fields = [
            'id',
            'name',
            'description',
            'course_id',
            'course_title',
            'required_completed_modules',
            'is_major_badge',
            'required_badges_count',
            'status',
            'earned',
            'pending',
            'rejected',
            'in_progress',
            'eligible',
            'completed_modules',
            'completed_badges',
        ]

    def get_course_title(self, obj):
        if not obj.course:
            return None
        return obj.course.title.get('en', 'Course')

    def get_earned(self, obj):
        status = self.get_status(obj)
        return status == UserBadge.STATUS_GRANTED

    def get_pending(self, obj):
        status = self.get_status(obj)
        return status == UserBadge.STATUS_PENDING

    def get_rejected(self, obj):
        status = self.get_status(obj)
        return status == UserBadge.STATUS_REJECTED

    def get_in_progress(self, obj):
        status = self.get_status(obj)
        return status == UserBadge.STATUS_IN_PROGRESS

    def get_status(self, obj):
        status_map = self.context.get('status_map', {})
        return status_map.get(obj.id)

    def get_completed_modules(self, obj):
        completed_count_map = self.context.get('completed_count_map', {})
        return completed_count_map.get(obj.id, 0)

    def get_completed_badges(self, obj):
        completed_badge_count_map = self.context.get('completed_badge_count_map', {})
        return completed_badge_count_map.get(obj.id, 0)

    def get_eligible(self, obj):
        if obj.is_major_badge:
            return self.get_completed_badges(obj) >= obj.required_badges_count
        return self.get_completed_modules(obj) >= obj.required_completed_modules


class UserBadgeSerializer(serializers.ModelSerializer):
    badge_name = serializers.CharField(source='badge.name', read_only=True)
    badge_description = serializers.CharField(source='badge.description', read_only=True)
    badge_required_completed_modules = serializers.IntegerField(source='badge.required_completed_modules', read_only=True)
    badge_required_badges_count = serializers.IntegerField(source='badge.required_badges_count', read_only=True)
    badge_is_major_badge = serializers.BooleanField(source='badge.is_major_badge', read_only=True)
    badge_course_id = serializers.IntegerField(source='badge.course.id', read_only=True)
    badge_course_title = serializers.SerializerMethodField()

    class Meta:
        model = UserBadge
        fields = [
            'id',
            'badge',
            'badge_name',
            'badge_description',
            'badge_required_completed_modules',
            'badge_required_badges_count',
            'badge_is_major_badge',
            'badge_course_id',
            'badge_course_title',
            'status',
            'is_awarded',
            'awarded_at',
            'revoked_at',
        ]

    def get_badge_course_title(self, obj):
        if not obj.badge.course:
            return None
        return obj.badge.course.title.get('en', 'Course')
