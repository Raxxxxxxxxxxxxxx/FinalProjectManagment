from pathlib import Path
from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


def company_document_path(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"private/company-documents/{instance.user_id}/{uuid4().hex}{extension}"


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "owner", _("Project owner")
        SUPPLIER = "supplier", _("Supplier")
        STAFF = "staff", _("Platform administration")

    role = models.CharField(_("Role"), max_length=20, choices=Role.choices, default=Role.OWNER)
    phone = models.CharField(_("Phone number"), max_length=30, blank=True)
    email_verified = models.BooleanField(_("Email verified"), default=False)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="unique_user_email_case_insensitive",
            ),
        ]

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_supplier(self):
        return self.role == self.Role.SUPPLIER


class CompanyProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="company")
    trade_name = models.CharField(_("Organization name"), max_length=180)
    business_type = models.CharField(_("Business type"), max_length=120, blank=True)
    city = models.CharField(_("City"), max_length=100, blank=True)
    description = models.TextField(_("About"), blank=True)
    registration_number = models.CharField(_("Commercial registration number"), max_length=100, blank=True)
    registration_document = models.FileField(
        _("Registration document"),
        upload_to=company_document_path,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
        blank=True,
    )
    is_verified = models.BooleanField(_("Verified"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.trade_name

    @property
    def completeness(self):
        values = [
            self.trade_name,
            self.business_type,
            self.city,
            self.description,
            self.registration_number,
            self.registration_document,
        ]
        return round(sum(bool(value) for value in values) * 100 / len(values))

    @property
    def document_name(self):
        return Path(self.registration_document.name).name if self.registration_document else ""
