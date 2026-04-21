import json
import base64
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import PasskeyCredential

User = get_user_model()

_ONE_PIXEL_PNG_BASE64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2Q2ioAAAAASUVORK5CYII='
)
_ONE_PIXEL_PNG_BYTES = base64.b64decode(_ONE_PIXEL_PNG_BASE64)


class _DummyDescriptor:
    def __init__(self, id):
        self.id = id


class _DummySelection:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _dummy_passkey_primitives():
    return {
        'base64url_to_bytes': lambda value: str(value).encode('utf-8'),
        'generate_authentication_options': lambda **kwargs: SimpleNamespace(challenge=b'auth-challenge'),
        'generate_registration_options': lambda **kwargs: SimpleNamespace(challenge=b'reg-challenge'),
        'options_to_json': lambda options: json.dumps({'challenge': 'encoded', 'timeout': 30000}),
        'verify_authentication_response': lambda **kwargs: SimpleNamespace(
            new_sign_count=9,
            credential_device_type='single_device',
            credential_backed_up=True,
        ),
        'verify_registration_response': lambda **kwargs: SimpleNamespace(
            credential_public_key=b'public-key',
            sign_count=1,
            credential_device_type='single_device',
            credential_backed_up=False,
        ),
        'InvalidAuthenticationResponse': ValueError,
        'InvalidRegistrationResponse': ValueError,
        'AuthenticatorSelectionCriteria': _DummySelection,
        'PublicKeyCredentialDescriptor': _DummyDescriptor,
        'ResidentKeyRequirement': SimpleNamespace(REQUIRED='required'),
        'UserVerificationRequirement': SimpleNamespace(REQUIRED='required'),
    }


class PasskeyApiTests(APITestCase):
    def setUp(self):
        self.password = 'TempPass123!'
        self.user = User.objects.create_user(
            username='learner1',
            email='learner@example.com',
            password=self.password,
            must_change_password=True,
        )

    def authenticate(self):
        self.client.force_authenticate(user=self.user)

    def test_password_login_includes_passkey_flags(self):
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id='cred-1',
            credential_public_key=b'key',
            sign_count=0,
            label='Laptop',
        )

        response = self.client.post(
            reverse('token_obtain_pair'),
            {'email': self.user.email, 'password': self.password},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['has_passkey'])
        self.assertEqual(response.data['passkey_count'], 1)
        self.assertTrue(response.data['must_change_password'])

    def test_change_password_returns_passkey_prompt_hint(self):
        self.authenticate()

        response = self.client.post(
            reverse('change_password'),
            {
                'currentPassword': self.password,
                'newPassword': 'BetterPass123!',
                'confirmPassword': 'BetterPass123!',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['passkey_setup_recommended'])
        self.assertFalse(response.data['has_passkey'])

    @patch('accounts.views._get_passkey_primitives', side_effect=_dummy_passkey_primitives)
    def test_passkey_status_and_disable_flow(self, mocked_primitives):
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id='cred-1',
            credential_public_key=b'key',
            sign_count=0,
            label='Phone',
        )
        self.authenticate()

        status_response = self.client.get(reverse('passkey_status'))
        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertTrue(status_response.data['available'])
        self.assertTrue(status_response.data['enabled'])
        self.assertEqual(status_response.data['count'], 1)

        disable_response = self.client.post(
            reverse('passkey_disable'),
            {'currentPassword': self.password},
            format='json',
        )
        self.assertEqual(disable_response.status_code, status.HTTP_200_OK)
        self.assertFalse(PasskeyCredential.objects.filter(user=self.user).exists())

    @patch('accounts.views._get_passkey_primitives', side_effect=_dummy_passkey_primitives)
    def test_passkey_registration_creates_credential(self, mocked_primitives):
        self.authenticate()

        options_response = self.client.post(
            reverse('passkey_register_options'),
            {'currentPassword': self.password, 'label': 'My Laptop'},
            format='json',
        )
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)
        self.assertIn('request_id', options_response.data)
        self.assertEqual(options_response.data['public_key']['challenge'], 'encoded')

        verify_response = self.client.post(
            reverse('passkey_register_verify'),
            {
                'requestId': options_response.data['request_id'],
                'credential': {
                    'id': 'cred-registered',
                    'response': {
                        'transports': ['internal'],
                    },
                },
            },
            format='json',
        )
        self.assertEqual(verify_response.status_code, status.HTTP_201_CREATED)

        credential = PasskeyCredential.objects.get(credential_id='cred-registered')
        self.assertEqual(credential.user, self.user)
        self.assertEqual(credential.label, 'My Laptop')
        self.assertEqual(credential.transports, ['internal'])

    @patch('accounts.views._get_passkey_primitives', side_effect=_dummy_passkey_primitives)
    def test_passkey_login_verify_returns_tokens(self, mocked_primitives):
        PasskeyCredential.objects.create(
            user=self.user,
            credential_id='cred-1',
            credential_public_key=b'key',
            sign_count=0,
            label='Phone',
        )

        options_response = self.client.post(
            reverse('passkey_login_options'),
            {'email': self.user.email},
            format='json',
        )
        self.assertEqual(options_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post(
            reverse('passkey_login_verify'),
            {
                'requestId': options_response.data['request_id'],
                'credential': {
                    'id': 'cred-1',
                    'response': {
                        'authenticatorData': 'x',
                        'clientDataJSON': 'y',
                        'signature': 'z',
                    },
                },
            },
            format='json',
        )

        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        self.assertIn('access', verify_response.data)
        self.assertIn('refresh', verify_response.data)

        credential = PasskeyCredential.objects.get(credential_id='cred-1')
        self.assertEqual(credential.sign_count, 9)
        self.assertIsNotNone(credential.last_used_at)


class ProfileApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='profileuser',
            email='profile@example.com',
            password='TempPass123!',
        )
        self.client.force_authenticate(user=self.user)

    @patch('accounts.serializers.generate_profile_image_url', return_value='')
    @patch('accounts.views.upload_profile_image', return_value='profiles/profileuser/image.png')
    def test_profile_patch_accepts_base64_image(self, mocked_upload, mocked_image_url):
        data_url = f'data:image/png;base64,{_ONE_PIXEL_PNG_BASE64}'
        response = self.client.patch(
            reverse('profile'),
            {
                'name': 'Profile User',
                'profile_image': data_url,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_image_path, 'profiles/profileuser/image.png')
        self.assertEqual(self.user.first_name, 'Profile')
        self.assertEqual(self.user.last_name, 'User')
        mocked_upload.assert_called_once()

    @patch('accounts.serializers.generate_profile_image_url', return_value='')
    @patch('accounts.views.upload_profile_image', return_value='profiles/profileuser/uploaded.png')
    def test_profile_patch_accepts_multipart_image(self, mocked_upload, mocked_image_url):
        uploaded_file = SimpleUploadedFile('avatar.png', _ONE_PIXEL_PNG_BYTES, content_type='image/png')
        response = self.client.patch(
            reverse('profile'),
            {
                'profile_image': uploaded_file,
            },
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_image_path, 'profiles/profileuser/uploaded.png')
        mocked_upload.assert_called_once()
