from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from datetime import timedelta

malaysia_phone_validator = RegexValidator(
    regex=r"^\+60\d{7,12}$",
    message="Phone number must start with +60 and contain digits only.",
)

class CustomUser(AbstractUser):
    USER_TYPE_LEARNER = 'learner'
    USER_TYPE_ADMIN = 'admin'
    USER_TYPE_CHOICES = (
        (USER_TYPE_LEARNER, 'Learner'),
        (USER_TYPE_ADMIN, 'Admin'),
    )
    email = models.EmailField(unique=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default=USER_TYPE_LEARNER)
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        default='',
        validators=[malaysia_phone_validator],
    )
    profile_image_path = models.CharField(max_length=500, blank=True, default='')
    birthdate = models.DateField(null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    groups = models.ManyToManyField(
        'auth.Group',
        related_name='customuser_set',
        blank=True,
        help_text='The groups this user belongs to.'
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='customuser_permissions_set',
        blank=True,
        help_text='Specific permissions for this user.'
    )
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']
    def save(self, *args, **kwargs):
        # Normalize Malaysia numbers into +60 format
        if self.phone_number:
            phone = self.phone_number.strip().replace(" ", "").replace("-", "")
            if phone.startswith("0"):
                phone = f"+60{phone[1:]}"
            elif not phone.startswith("+60"):
                phone = f"+60{phone}"
            self.phone_number = phone
        if self.is_staff or self.is_superuser:
            self.user_type = self.USER_TYPE_ADMIN
            self.is_staff = True
        elif not self.user_type:
            self.user_type = self.USER_TYPE_LEARNER
        super().save(*args, **kwargs)


class AccountApplication(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED = 'denied'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_DENIED, 'Denied'),
    )

    full_name = models.CharField(max_length=255)
    email = models.EmailField(db_index=True)
    phone_number = models.CharField(max_length=20, validators=[malaysia_phone_validator])
    birthdate = models.DateField()
    cv_file = models.FileField(upload_to='applications/cv/', blank=True)
    cv_storage_key = models.CharField(max_length=500, blank=True, default='')
    cv_original_name = models.CharField(max_length=255, blank=True, default='')
    cv_content_type = models.CharField(max_length=255, blank=True, default='')
    cv_size = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    admin_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        'accounts.CustomUser',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_account_applications',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_user = models.OneToOneField(
        'accounts.CustomUser',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_application',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.full_name} ({self.email}) - {self.status}'

    def mark_reviewed(self, reviewer, status, notes=''):
        self.status = status
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.admin_notes = notes
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes', 'updated_at'])


class PasswordResetCode(models.Model):
    user = models.ForeignKey('accounts.CustomUser', on_delete=models.CASCADE, related_name='password_reset_codes')
    code = models.CharField(max_length=6, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    @classmethod
    def create_for_user(cls, user, code):
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        return cls.objects.create(
            user=user,
            code=code,
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class PasskeyCredential(models.Model):
    user = models.ForeignKey(
        'accounts.CustomUser',
        on_delete=models.CASCADE,
        related_name='passkey_credentials',
    )
    credential_id = models.CharField(max_length=512, unique=True, db_index=True)
    credential_public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    credential_device_type = models.CharField(max_length=32, blank=True, default='')
    credential_backed_up = models.BooleanField(default=False)
    label = models.CharField(max_length=120, blank=True, default='')
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        name = self.label or 'Passkey'
        return f'{self.user.email} - {name}'


class TwoFactorAuth(models.Model):
    user = models.OneToOneField(
        'accounts.CustomUser',
        on_delete=models.CASCADE,
        related_name='two_factor_auth',
    )
    secret = models.CharField(max_length=64, blank=True, default='')
    is_enabled = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Two-factor authentication'
        verbose_name_plural = 'Two-factor authentication'

    def __str__(self):
        state = 'enabled' if self.is_enabled else 'disabled'
        return f'{self.user.email} - authenticator ({state})'
