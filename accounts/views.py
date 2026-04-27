# accounts/views.py
import json
import hashlib
import hmac
import os
import random
import struct
import time
import uuid
from base64 import b32decode, b32encode
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils import timezone
from notifications.models import Notification, UserNotification
from rest_framework import generics, permissions, serializers, status, throttling
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import AccountApplication, PasskeyCredential, PasswordResetCode, TwoFactorAuth
from .serializers import (
    AccountApplicationSerializer,
    PasskeyCredentialSerializer,
    ProfileSerializer,
    RegisterSerializer,
    TwoFactorAuthSerializer,
)
from .services import delete_profile_image, upload_profile_image

User = get_user_model()

PASSKEY_REGISTER_CACHE_PREFIX = 'passkey_register'
PASSKEY_AUTH_CACHE_PREFIX = 'passkey_auth'
PASSKEY_CACHE_TIMEOUT = 300
TWO_FACTOR_LOGIN_CACHE_PREFIX = 'two_factor_login'
TWO_FACTOR_CACHE_TIMEOUT = 300
TWO_FACTOR_ISSUER = 'Park Guide App'


def _build_user_payload(user):
    passkey_count = user.passkey_credentials.count()
    two_factor = getattr(user, 'two_factor_auth', None)
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'user_type': user.user_type,
        'must_change_password': user.must_change_password,
        'has_passkey': passkey_count > 0,
        'passkey_count': passkey_count,
        'has_authenticator_2fa': bool(two_factor and two_factor.is_enabled),
    }


def _build_auth_response(user):
    refresh = RefreshToken.for_user(user)
    role = 'admin' if (user.is_staff or user.is_superuser or user.user_type == 'admin') else 'learner'
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': _build_user_payload(user),
        'role': role,
        'must_change_password': user.must_change_password,
        'has_passkey': user.passkey_credentials.exists(),
        'passkey_count': user.passkey_credentials.count(),
        'passkey_setup_recommended': not user.must_change_password and not user.passkey_credentials.exists(),
    }


def _get_webauthn_dependencies():
    try:
        from webauthn import (
            base64url_to_bytes,
            generate_authentication_options,
            generate_registration_options,
            options_to_json,
            verify_authentication_response,
            verify_registration_response,
        )
        from webauthn.helpers.exceptions import (
            InvalidAuthenticationResponse,
            InvalidRegistrationResponse,
        )
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            PublicKeyCredentialDescriptor,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )
    except ImportError:
        return None

    return {
        'base64url_to_bytes': base64url_to_bytes,
        'generate_authentication_options': generate_authentication_options,
        'generate_registration_options': generate_registration_options,
        'options_to_json': options_to_json,
        'verify_authentication_response': verify_authentication_response,
        'verify_registration_response': verify_registration_response,
        'InvalidAuthenticationResponse': InvalidAuthenticationResponse,
        'InvalidRegistrationResponse': InvalidRegistrationResponse,
        'AuthenticatorSelectionCriteria': AuthenticatorSelectionCriteria,
        'PublicKeyCredentialDescriptor': PublicKeyCredentialDescriptor,
        'ResidentKeyRequirement': ResidentKeyRequirement,
        'UserVerificationRequirement': UserVerificationRequirement,
    }


def _passkey_settings_ready():
    return bool(
        getattr(settings, 'PASSKEY_RP_ID', '').strip()
        and getattr(settings, 'PASSKEY_RP_NAME', '').strip()
        and getattr(settings, 'PASSKEY_ORIGIN', '').strip()
    )


def _passkey_unavailable_response():
    return Response(
        {
            'detail': (
                'Passkey support is not available. Install the `webauthn` package and '
                'configure PASSKEY_RP_ID, PASSKEY_RP_NAME, and PASSKEY_ORIGIN.'
            )
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _get_passkey_primitives():
    dependencies = _get_webauthn_dependencies()
    if not dependencies or not _passkey_settings_ready():
        return None
    return dependencies


def _require_current_password(request):
    password = request.data.get('currentPassword')
    if not password:
        return Response({'detail': 'Current password is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if not request.user.check_password(password):
        return Response({'detail': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _normalize_email(value):
    return str(value or '').strip().lower()


def _get_or_create_two_factor(user):
    two_factor, _ = TwoFactorAuth.objects.get_or_create(user=user)
    return two_factor


def _generate_totp_secret():
    return b32encode(os.urandom(20)).decode('ascii').rstrip('=')


def _decode_totp_secret(secret):
    normalized = str(secret or '').strip().replace(' ', '').upper()
    if not normalized:
        raise ValueError('Missing TOTP secret.')
    padding = '=' * ((8 - len(normalized) % 8) % 8)
    return b32decode(normalized + padding, casefold=True)


def _totp_time_step(for_time=None, interval=30):
    timestamp = int(for_time if for_time is not None else time.time())
    return timestamp // interval


def _generate_totp_code(secret, step=None, digits=6):
    key = _decode_totp_secret(secret)
    counter = _totp_time_step() if step is None else int(step)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)


def _verify_totp_code(two_factor, code, allowed_drift=1, mark_used=False):
    normalized_code = ''.join(ch for ch in str(code or '') if ch.isdigit())
    if len(normalized_code) != 6:
        return False

    current_step = _totp_time_step()
    for offset in range(-allowed_drift, allowed_drift + 1):
        step = current_step + offset
        if step < 0:
            continue
        if hmac.compare_digest(_generate_totp_code(two_factor.secret, step=step), normalized_code):
            if mark_used:
                if two_factor.last_used_step == step:
                    return False
                two_factor.last_used_step = step
                two_factor.save(update_fields=['last_used_step', 'updated_at'])
            return True
    return False


def _build_totp_setup_payload(user, secret):
    label = quote(_normalize_email(user.email))
    issuer = quote(TWO_FACTOR_ISSUER)
    return {
        'secret': secret,
        'issuer': TWO_FACTOR_ISSUER,
        'account_name': user.email,
        'otpauth_uri': (
            f'otpauth://totp/{issuer}:{label}'
            f'?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30'
        ),
    }


def _cache_two_factor_login(user):
    request_id = uuid.uuid4().hex
    cache.set(
        _cache_key(TWO_FACTOR_LOGIN_CACHE_PREFIX, request_id),
        {'user_id': user.id},
        timeout=TWO_FACTOR_CACHE_TIMEOUT,
    )
    return request_id


def _cache_key(prefix, request_id):
    return f'{prefix}:{request_id}'


def _serialize_options(options, options_to_json):
    return json.loads(options_to_json(options))


def _get_credential_id_from_payload(payload):
    return str(payload.get('id') or payload.get('rawId') or '').strip()


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'register'


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self):
        return self.request.user

    def partial_update(self, request, *args, **kwargs):
        user = self.get_object()
        previous_profile_image = user.profile_image_path
        serializer = self.get_serializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        uploaded_profile_image = serializer.validated_data.get('profile_image')
        self.perform_update(serializer)

        if uploaded_profile_image:
            user.profile_image_path = upload_profile_image(uploaded_profile_image, user)
            user.save(update_fields=['profile_image_path'])
            if previous_profile_image and previous_profile_image != user.profile_image_path:
                delete_profile_image(previous_profile_image)

        refreshed = self.get_serializer(user)
        return Response(refreshed.data)


class AccountApplicationCreateView(generics.CreateAPIView):
    serializer_class = AccountApplicationSerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        application = serializer.save()

        staff_users = User.objects.filter(is_active=True, is_staff=True)
        if not staff_users.exists():
            return

        notification = Notification.objects.create(
            title='New Park Guide application',
            description=f'{application.full_name} submitted a registration application.',
            full_text=(
                f'Applicant: {application.full_name}\n'
                f'Email: {application.email}\n'
                f'Phone: {application.phone_number}\n'
                'Please review in Dashboard > Users.'
            ),
            audience_type=Notification.AUDIENCE_ADMINS,
            tracking_type=Notification.TRACKING_USER_ACK,
            show_in_header=True,
        )

        UserNotification.objects.bulk_create(
            [UserNotification(user=user, notification=notification) for user in staff_users],
            ignore_conflicts=True,
        )


class ChangePasswordView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        current_password = request.data.get('currentPassword')
        new_password = request.data.get('newPassword')
        confirm_password = request.data.get('confirmPassword')

        if not current_password or not new_password or not confirm_password:
            return Response({'code': 'FILL_ALL_PASSWORD_FIELDS'}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({'code': 'PASSWORDS_DO_NOT_MATCH'}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.check_password(current_password):
            return Response({'code': 'CURRENT_PASSWORD_INCORRECT'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(new_password, user=user)
        except ValidationError as exc:
            return Response(
                {
                    'code': 'PASSWORD_POLICY_FAILED',
                    'detail': exc.messages[0] if exc.messages else 'Password does not meet policy.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])
        return Response(
            {
                'detail': 'Password updated.',
                'passkey_setup_recommended': not user.passkey_credentials.exists(),
                'has_passkey': user.passkey_credentials.exists(),
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordRequestView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'password_reset'

    def post(self, request, *args, **kwargs):
        email = str(request.data.get('email') or '').strip().lower()
        user = User.objects.filter(email=email, is_active=True).first()

        if not user:
            return Response({'detail': 'If your email exists, a reset code has been sent.'}, status=status.HTTP_200_OK)

        code = f'{random.randint(0, 999999):06d}'
        PasswordResetCode.create_for_user(user=user, code=code)

        send_mail(
            subject='Park Guide password reset code',
            message=(
                f'Hello {user.first_name or user.username},\n\n'
                'Use this verification code to reset your password:\n\n'
                f'{code}\n\n'
                'This code expires in 15 minutes.\n\n'
                'If you did not request this, please ignore this message.'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[user.email],
            fail_silently=True,
        )

        return Response({'detail': 'If your email exists, a reset code has been sent.'}, status=status.HTTP_200_OK)


class ForgotPasswordConfirmView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        email = str(request.data.get('email') or '').strip().lower()
        code = str(request.data.get('code') or '').strip()
        new_password = request.data.get('newPassword')
        confirm_password = request.data.get('confirmPassword')

        if not email or not code or not new_password or not confirm_password:
            return Response({'detail': 'All fields are required.'}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({'detail': 'Passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email, is_active=True).first()
        if not user:
            return Response({'detail': 'Invalid reset request.'}, status=status.HTTP_400_BAD_REQUEST)

        reset_row = (
            PasswordResetCode.objects.filter(user=user, code=code, used_at__isnull=True)
            .order_by('-created_at')
            .first()
        )
        if not reset_row or not reset_row.is_valid():
            return Response({'detail': 'Code is invalid or expired.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password(new_password, user=user)
        except ValidationError as exc:
            return Response(
                {'detail': exc.messages[0] if exc.messages else 'Password does not meet policy.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])

        reset_row.used_at = timezone.now()
        reset_row.save(update_fields=['used_at'])

        return Response(
            {
                'detail': 'Password reset successful.',
                'passkey_setup_recommended': not user.passkey_credentials.exists(),
                'has_passkey': user.passkey_credentials.exists(),
            },
            status=status.HTTP_200_OK,
        )


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = 'email'

    def validate(self, attrs):
        email = _normalize_email(attrs.get('email'))
        password = attrs.get('password')

        if not email or not password:
            raise serializers.ValidationError({'detail': 'Email and password are required.'})

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise serializers.ValidationError({'detail': 'No active account found with the given credentials'})

        if not user.check_password(password):
            raise serializers.ValidationError({'detail': 'No active account found with the given credentials'})

        attrs['username'] = user.username
        self.user = user
        return super().validate(attrs)


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        email = _normalize_email(request.data.get('email'))
        password = request.data.get('password')

        if not email or not password:
            return Response({'detail': 'Email and password are required.'}, status=status.HTTP_401_UNAUTHORIZED)

        user_obj = User.objects.filter(email=email, is_active=True).first()
        if not user_obj or not user_obj.check_password(password):
            return Response({'detail': 'No active account found with the given credentials'}, status=status.HTTP_401_UNAUTHORIZED)

        two_factor = getattr(user_obj, 'two_factor_auth', None)
        if two_factor and two_factor.is_enabled and two_factor.secret:
            return Response(
                {
                    'requires_2fa': True,
                    'request_id': _cache_two_factor_login(user_obj),
                    'detail': 'Authenticator code required.',
                    'email': user_obj.email,
                },
                status=status.HTTP_200_OK,
            )

        return Response(_build_auth_response(user_obj), status=status.HTTP_200_OK)


class TwoFactorStatusView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        two_factor = getattr(request.user, 'two_factor_auth', None)
        return Response(
            {
                'available': True,
                'enabled': bool(two_factor and two_factor.is_enabled),
                'has_setup_secret': bool(two_factor and two_factor.secret),
                'details': TwoFactorAuthSerializer(two_factor).data if two_factor else None,
            },
            status=status.HTTP_200_OK,
        )


class TwoFactorSetupView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        password_error = _require_current_password(request)
        if password_error:
            return password_error

        two_factor = _get_or_create_two_factor(request.user)
        secret = _generate_totp_secret()
        two_factor.secret = secret
        two_factor.is_enabled = False
        two_factor.confirmed_at = None
        two_factor.last_used_step = None
        two_factor.save(update_fields=['secret', 'is_enabled', 'confirmed_at', 'last_used_step', 'updated_at'])

        payload = _build_totp_setup_payload(request.user, secret)
        payload.update({'enabled': False})
        return Response(payload, status=status.HTTP_200_OK)


class TwoFactorConfirmView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        code = request.data.get('code')
        two_factor = getattr(request.user, 'two_factor_auth', None)
        if not two_factor or not two_factor.secret:
            return Response({'detail': 'Set up authenticator first.'}, status=status.HTTP_400_BAD_REQUEST)

        if not _verify_totp_code(two_factor, code, mark_used=True):
            return Response({'detail': 'Authenticator code is invalid or expired.'}, status=status.HTTP_400_BAD_REQUEST)

        two_factor.is_enabled = True
        two_factor.confirmed_at = timezone.now()
        two_factor.save(update_fields=['is_enabled', 'confirmed_at', 'updated_at'])
        return Response({'detail': 'Authenticator 2FA enabled.', 'enabled': True}, status=status.HTTP_200_OK)


class TwoFactorDisableView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        password_error = _require_current_password(request)
        if password_error:
            return password_error

        code = request.data.get('code')
        two_factor = getattr(request.user, 'two_factor_auth', None)
        if not two_factor or not two_factor.is_enabled:
            return Response({'detail': 'Authenticator 2FA is not enabled.'}, status=status.HTTP_400_BAD_REQUEST)

        if not _verify_totp_code(two_factor, code, mark_used=False):
            return Response({'detail': 'Authenticator code is invalid or expired.'}, status=status.HTTP_400_BAD_REQUEST)

        two_factor.secret = ''
        two_factor.is_enabled = False
        two_factor.confirmed_at = None
        two_factor.last_used_step = None
        two_factor.save(update_fields=['secret', 'is_enabled', 'confirmed_at', 'last_used_step', 'updated_at'])
        return Response({'detail': 'Authenticator 2FA disabled.', 'enabled': False}, status=status.HTTP_200_OK)


class TwoFactorLoginVerifyView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        request_id = str(request.data.get('requestId') or request.data.get('request_id') or '').strip()
        code = request.data.get('code')
        if not request_id:
            return Response({'detail': 'requestId is required.'}, status=status.HTTP_400_BAD_REQUEST)

        cached = cache.get(_cache_key(TWO_FACTOR_LOGIN_CACHE_PREFIX, request_id))
        if not cached:
            return Response({'detail': 'Two-factor login request expired or invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(id=cached.get('user_id'), is_active=True).first()
        if not user:
            return Response({'detail': 'Login request is invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        two_factor = getattr(user, 'two_factor_auth', None)
        if not two_factor or not two_factor.is_enabled or not two_factor.secret:
            return Response({'detail': 'Authenticator 2FA is not enabled for this account.'}, status=status.HTTP_400_BAD_REQUEST)

        if not _verify_totp_code(two_factor, code, mark_used=True):
            return Response({'detail': 'Authenticator code is invalid or expired.'}, status=status.HTTP_401_UNAUTHORIZED)

        cache.delete(_cache_key(TWO_FACTOR_LOGIN_CACHE_PREFIX, request_id))
        return Response(_build_auth_response(user), status=status.HTTP_200_OK)


class PasskeyStatusView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        credentials = request.user.passkey_credentials.all()
        return Response(
            {
                'available': _get_passkey_primitives() is not None,
                'enabled': credentials.exists(),
                'count': credentials.count(),
                'credentials': PasskeyCredentialSerializer(credentials, many=True).data,
            },
            status=status.HTTP_200_OK,
        )


class PasskeyRegisterOptionsView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        primitives = _get_passkey_primitives()
        if not primitives:
            return _passkey_unavailable_response()

        password_error = _require_current_password(request)
        if password_error:
            return password_error

        user = request.user
        PublicKeyCredentialDescriptor = primitives['PublicKeyCredentialDescriptor']
        AuthenticatorSelectionCriteria = primitives['AuthenticatorSelectionCriteria']
        ResidentKeyRequirement = primitives['ResidentKeyRequirement']
        UserVerificationRequirement = primitives['UserVerificationRequirement']

        options = primitives['generate_registration_options'](
            rp_id=settings.PASSKEY_RP_ID,
            rp_name=settings.PASSKEY_RP_NAME,
            user_id=str(user.id).encode('utf-8'),
            user_name=user.email,
            user_display_name=user.get_full_name() or user.email,
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=primitives['base64url_to_bytes'](credential.credential_id))
                for credential in user.passkey_credentials.all()
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )

        request_id = uuid.uuid4().hex
        cache.set(
            _cache_key(PASSKEY_REGISTER_CACHE_PREFIX, request_id),
            {
                'user_id': user.id,
                'challenge': options.challenge,
                'label': str(request.data.get('label') or '').strip(),
            },
            timeout=PASSKEY_CACHE_TIMEOUT,
        )

        return Response(
            {
                'request_id': request_id,
                'public_key': _serialize_options(options, primitives['options_to_json']),
            },
            status=status.HTTP_200_OK,
        )


class PasskeyRegisterVerifyView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        primitives = _get_passkey_primitives()
        if not primitives:
            return _passkey_unavailable_response()

        request_id = str(request.data.get('requestId') or request.data.get('request_id') or '').strip()
        if not request_id:
            return Response({'detail': 'requestId is required.'}, status=status.HTTP_400_BAD_REQUEST)

        cached = cache.get(_cache_key(PASSKEY_REGISTER_CACHE_PREFIX, request_id))
        if not cached or cached.get('user_id') != request.user.id:
            return Response({'detail': 'Challenge expired or invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            verification = primitives['verify_registration_response'](
                credential=request.data.get('credential') or request.data,
                expected_challenge=cached['challenge'],
                expected_origin=settings.PASSKEY_ORIGIN,
                expected_rp_id=settings.PASSKEY_RP_ID,
                require_user_verification=True,
            )
        except primitives['InvalidRegistrationResponse'] as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        credential_payload = request.data.get('credential') or request.data
        credential_id = _get_credential_id_from_payload(credential_payload)
        if not credential_id:
            return Response({'detail': 'Credential id is missing.'}, status=status.HTTP_400_BAD_REQUEST)

        PasskeyCredential.objects.update_or_create(
            credential_id=credential_id,
            defaults={
                'user': request.user,
                'credential_public_key': verification.credential_public_key,
                'sign_count': verification.sign_count,
                'transports': credential_payload.get('response', {}).get('transports', []),
                'credential_device_type': str(getattr(verification, 'credential_device_type', '') or ''),
                'credential_backed_up': bool(getattr(verification, 'credential_backed_up', False)),
                'label': cached.get('label', ''),
            },
        )

        cache.delete(_cache_key(PASSKEY_REGISTER_CACHE_PREFIX, request_id))
        return Response(
            {
                'detail': 'Passkey registered successfully.',
                'has_passkey': True,
                'passkey_count': request.user.passkey_credentials.count(),
            },
            status=status.HTTP_201_CREATED,
        )


class PasskeyAuthenticationOptionsView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        primitives = _get_passkey_primitives()
        if not primitives:
            return _passkey_unavailable_response()

        email = str(request.data.get('email') or '').strip().lower()
        credentials_qs = PasskeyCredential.objects.select_related('user').all()

        if email:
            credentials_qs = credentials_qs.filter(user__email=email, user__is_active=True)
        else:
            credentials_qs = credentials_qs.filter(user__is_active=True)

        credentials = list(credentials_qs)
        if email and not credentials:
            return Response({'detail': 'No passkey is registered for this account.'}, status=status.HTTP_400_BAD_REQUEST)

        allow_credentials = None
        if email:
            PublicKeyCredentialDescriptor = primitives['PublicKeyCredentialDescriptor']
            allow_credentials = [
                PublicKeyCredentialDescriptor(id=primitives['base64url_to_bytes'](credential.credential_id))
                for credential in credentials
            ]

        option_kwargs = {
            'rp_id': settings.PASSKEY_RP_ID,
            'user_verification': primitives['UserVerificationRequirement'].REQUIRED,
        }
        if allow_credentials is not None:
            option_kwargs['allow_credentials'] = allow_credentials

        options = primitives['generate_authentication_options'](**option_kwargs)

        request_id = uuid.uuid4().hex
        cache.set(
            _cache_key(PASSKEY_AUTH_CACHE_PREFIX, request_id),
            {
                'challenge': options.challenge,
                'email': email,
            },
            timeout=PASSKEY_CACHE_TIMEOUT,
        )

        return Response(
            {
                'request_id': request_id,
                'public_key': _serialize_options(options, primitives['options_to_json']),
            },
            status=status.HTTP_200_OK,
        )


class PasskeyAuthenticationVerifyView(generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        primitives = _get_passkey_primitives()
        if not primitives:
            return _passkey_unavailable_response()

        request_id = str(request.data.get('requestId') or request.data.get('request_id') or '').strip()
        if not request_id:
            return Response({'detail': 'requestId is required.'}, status=status.HTTP_400_BAD_REQUEST)

        cached = cache.get(_cache_key(PASSKEY_AUTH_CACHE_PREFIX, request_id))
        if not cached:
            return Response({'detail': 'Challenge expired or invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        credential_payload = request.data.get('credential') or request.data
        credential_id = _get_credential_id_from_payload(credential_payload)
        if not credential_id:
            return Response({'detail': 'Credential id is missing.'}, status=status.HTTP_400_BAD_REQUEST)

        credential = PasskeyCredential.objects.select_related('user').filter(credential_id=credential_id).first()
        if not credential or not credential.user.is_active:
            return Response({'detail': 'Passkey not recognized.'}, status=status.HTTP_401_UNAUTHORIZED)

        if cached.get('email') and credential.user.email.lower() != cached['email']:
            return Response({'detail': 'Passkey does not match this account.'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            verification = primitives['verify_authentication_response'](
                credential=credential_payload,
                expected_challenge=cached['challenge'],
                expected_rp_id=settings.PASSKEY_RP_ID,
                expected_origin=settings.PASSKEY_ORIGIN,
                credential_public_key=bytes(credential.credential_public_key),
                credential_current_sign_count=credential.sign_count,
                require_user_verification=True,
            )
        except primitives['InvalidAuthenticationResponse'] as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        credential.sign_count = verification.new_sign_count
        credential.credential_device_type = str(getattr(verification, 'credential_device_type', '') or '')
        credential.credential_backed_up = bool(getattr(verification, 'credential_backed_up', False))
        credential.last_used_at = timezone.now()
        credential.save(
            update_fields=[
                'sign_count',
                'credential_device_type',
                'credential_backed_up',
                'last_used_at',
                'updated_at',
            ]
        )
        cache.delete(_cache_key(PASSKEY_AUTH_CACHE_PREFIX, request_id))

        return Response(_build_auth_response(credential.user), status=status.HTTP_200_OK)


class PasskeyDisableView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        password_error = _require_current_password(request)
        if password_error:
            return password_error

        deleted_count, _ = request.user.passkey_credentials.all().delete()
        return Response(
            {
                'detail': 'Passkey access disabled.',
                'deleted_credentials': deleted_count,
                'has_passkey': False,
            },
            status=status.HTTP_200_OK,
        )
