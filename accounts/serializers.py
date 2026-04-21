import base64
import binascii
import uuid

from django.core.files.base import ContentFile
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import AccountApplication, PasskeyCredential, TwoFactorAuth
from .services import generate_profile_image_url, upload_application_cv


User = get_user_model()


class FlexibleProfileImageField(serializers.FileField):
    """Accept multipart file uploads and base64 data URLs from web/mobile clients."""

    default_error_messages = {
        'invalid_string': 'Please upload an image file using multipart form-data or a base64 data URL.',
        'invalid_base64': 'Profile image data URL is invalid or corrupted.',
    }

    _MIME_TO_EXTENSION = {
        'image/jpeg': 'jpg',
        'image/jpg': 'jpg',
        'image/png': 'png',
        'image/webp': 'webp',
        'image/heic': 'heic',
        'image/heif': 'heif',
    }

    def to_internal_value(self, data):
        if isinstance(data, str):
            raw_value = data.strip()
            if not raw_value:
                return None

            if raw_value.startswith('data:image/') and ';base64,' in raw_value:
                header, encoded = raw_value.split(';base64,', 1)
                mime_type = header.replace('data:', '').lower()
                extension = self._MIME_TO_EXTENSION.get(mime_type, 'jpg')

                try:
                    decoded_file = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError, binascii.Error):
                    self.fail('invalid_base64')

                generated_name = f'profile_{uuid.uuid4().hex}.{extension}'
                data = ContentFile(decoded_file, name=generated_name)
                data.content_type = mime_type
            else:
                self.fail('invalid_string')

        return super().to_internal_value(data)

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']


class ProfileSerializer(serializers.ModelSerializer):
    name = serializers.CharField(required=False, allow_blank=False)
    phone = serializers.CharField(source='phone_number', required=False, allow_blank=True)
    role = serializers.SerializerMethodField(read_only=True)
    profile_image_url = serializers.SerializerMethodField(read_only=True)
    profile_image = FlexibleProfileImageField(write_only=True, required=False)

    MAX_PROFILE_IMAGE_BYTES = 5 * 1024 * 1024
    ALLOWED_PROFILE_IMAGE_TYPES = {
        'image/jpeg',
        'image/jpg',
        'image/png',
        'image/webp',
        'image/heic',
        'image/heif',
    }

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'name',
            'phone',
            'profile_image',
            'profile_image_url',
            'user_type',
            'role',
        ]
        read_only_fields = ['id', 'username', 'user_type', 'role', 'profile_image_url']

    def get_role(self, obj):
        return 'admin' if (obj.is_staff or obj.is_superuser or obj.user_type == 'admin') else 'learner'

    def get_profile_image_url(self, obj):
        return generate_profile_image_url(getattr(obj, 'profile_image_path', ''))

    def to_representation(self, instance):
        data = super().to_representation(instance)
        full_name = f"{instance.first_name} {instance.last_name}".strip()
        data['name'] = full_name or instance.username or instance.email
        return data

    def validate_email(self, value):
        email = (value or '').strip().lower()
        user = self.instance
        if User.objects.exclude(pk=getattr(user, 'pk', None)).filter(email=email).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return email

    def validate_phone(self, value):
        cleaned = (value or '').strip().replace(' ', '').replace('-', '')
        if not cleaned:
            return ''
        if cleaned.startswith('0'):
            cleaned = f'+60{cleaned[1:]}'
        elif not cleaned.startswith('+60'):
            cleaned = f'+60{cleaned}'
        return cleaned

    def validate_name(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 2:
            raise serializers.ValidationError('Please provide your full name.')
        return cleaned

    def validate_profile_image(self, value):
        if not value:
            return value

        content_type = str(getattr(value, 'content_type', '') or '').lower()
        if content_type and content_type not in self.ALLOWED_PROFILE_IMAGE_TYPES:
            raise serializers.ValidationError('Please upload a JPG, PNG, WEBP, or HEIC image.')

        file_size = int(getattr(value, 'size', 0) or 0)
        if file_size > self.MAX_PROFILE_IMAGE_BYTES:
            raise serializers.ValidationError('Profile images must be 5 MB or smaller.')

        return value

    def update(self, instance, validated_data):
        validated_data.pop('profile_image', None)
        full_name = validated_data.pop('name', None)
        if full_name is not None:
            name_parts = full_name.split(None, 1)
            instance.first_name = name_parts[0]
            instance.last_name = name_parts[1] if len(name_parts) > 1 else ''

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user


class AccountApplicationSerializer(serializers.ModelSerializer):
    cv_file = serializers.FileField(write_only=True)

    class Meta:
        model = AccountApplication
        fields = [
            'id',
            'full_name',
            'email',
            'phone_number',
            'birthdate',
            'cv_file',
            'cv_original_name',
            'cv_size',
            'status',
            'created_at',
        ]
        read_only_fields = ['id', 'status', 'created_at', 'cv_original_name', 'cv_size']

    def validate_full_name(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 3:
            raise serializers.ValidationError('Please provide your full name.')
        return cleaned

    def validate_phone_number(self, value):
        cleaned = (value or '').strip().replace(' ', '').replace('-', '')
        if cleaned.startswith('0'):
            cleaned = f'+60{cleaned[1:]}'
        elif not cleaned.startswith('+60'):
            cleaned = f'+60{cleaned}'
        return cleaned

    def validate_email(self, value):
        return (value or '').strip().lower()

    def validate(self, attrs):
        email = attrs.get('email')
        pending_exists = AccountApplication.objects.filter(
            email=email,
            status=AccountApplication.STATUS_PENDING,
        ).exists()
        if pending_exists:
            raise serializers.ValidationError({'email': 'You already have a pending application.'})

        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError({'email': 'An account with this email already exists.'})

        return attrs

    def create(self, validated_data):
        uploaded_cv = validated_data.pop('cv_file')
        email = validated_data.get('email', '')
        uploaded = upload_application_cv(uploaded_cv, applicant_email=email)
        validated_data.update(
            {
                'cv_storage_key': uploaded['storage_key'],
                'cv_original_name': uploaded['original_name'],
                'cv_content_type': uploaded['content_type'],
                'cv_size': uploaded['size'],
            }
        )
        return super().create(validated_data)


class PasskeyCredentialSerializer(serializers.ModelSerializer):
    class Meta:
        model = PasskeyCredential
        fields = [
            'id',
            'label',
            'credential_device_type',
            'credential_backed_up',
            'transports',
            'last_used_at',
            'created_at',
        ]


class TwoFactorAuthSerializer(serializers.ModelSerializer):
    class Meta:
        model = TwoFactorAuth
        fields = [
            'is_enabled',
            'confirmed_at',
            'updated_at',
        ]
