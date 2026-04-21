import datetime
import os
import re

import firebase_admin
from django.conf import settings
from firebase_admin import credentials, storage


def _ensure_firebase_initialized():
    if firebase_admin._apps:
        return

    cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT_PATH)
    firebase_admin.initialize_app(
        cred,
        {
            'storageBucket': settings.FIREBASE_STORAGE_BUCKET,
        },
    )


def _safe_name(value):
    return re.sub(r'[^a-zA-Z0-9._-]+', '_', value or '')


def upload_application_cv(uploaded_file, applicant_email=''):
    _ensure_firebase_initialized()

    bucket = storage.bucket()
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    email_segment = _safe_name((applicant_email or 'anonymous').split('@')[0]) or 'anonymous'
    file_name = _safe_name(uploaded_file.name) or f'cv_{timestamp}.pdf'
    blob_path = os.path.join('applications', 'cv', email_segment, f'{timestamp}_{file_name}').replace('\\', '/')

    blob = bucket.blob(blob_path)
    blob.upload_from_file(uploaded_file, content_type=getattr(uploaded_file, 'content_type', None))

    return {
        'storage_key': blob_path,
        'original_name': uploaded_file.name,
        'content_type': getattr(uploaded_file, 'content_type', ''),
        'size': getattr(uploaded_file, 'size', 0),
    }


def generate_application_cv_url(storage_key, expires_seconds=3600):
    if not storage_key:
        return ''

    _ensure_firebase_initialized()
    bucket = storage.bucket()
    blob = bucket.blob(storage_key)
    return blob.generate_signed_url(datetime.timedelta(seconds=expires_seconds), method='GET')


def delete_application_cv(storage_key):
    if not storage_key:
        return

    _ensure_firebase_initialized()
    bucket = storage.bucket()
    blob = bucket.blob(storage_key)
    blob.delete()


def upload_profile_image(uploaded_file, user):
    _ensure_firebase_initialized()

    bucket = storage.bucket()
    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')
    user_segment = _safe_name(user.email.split('@')[0] if user and user.email else user.username or 'user') or 'user'
    file_name = _safe_name(uploaded_file.name) or f'profile_{timestamp}.jpg'
    blob_path = os.path.join('profiles', user_segment, f'{timestamp}_{file_name}').replace('\\', '/')

    blob = bucket.blob(blob_path)
    blob.upload_from_file(uploaded_file, content_type=getattr(uploaded_file, 'content_type', None))

    return blob_path


def generate_profile_image_url(storage_key, expires_seconds=3600):
    if not storage_key:
        return ''

    _ensure_firebase_initialized()
    bucket = storage.bucket()
    blob = bucket.blob(storage_key)
    return blob.generate_signed_url(datetime.timedelta(seconds=expires_seconds), method='GET')


def delete_profile_image(storage_key):
    if not storage_key:
        return

    _ensure_firebase_initialized()
    bucket = storage.bucket()
    blob = bucket.blob(storage_key)
    try:
        blob.delete()
    except Exception:
        pass
