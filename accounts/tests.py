import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import CompanyProfileForm, RegisterForm
from .models import CompanyProfile, User


class AccountAndVerificationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_directory = tempfile.TemporaryDirectory()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media_directory.name)
        cls.media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.media_override.disable()
        cls.media_directory.cleanup()

    def setUp(self):
        self.supplier = User.objects.create_user(
            username="profile_supplier",
            email="supplier@example.com",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        self.company = CompanyProfile.objects.create(
            user=self.supplier,
            trade_name="Verified Supplier",
            business_type="Equipment",
            city="Damascus",
            description="A verified test supplier.",
            registration_number="REG-100",
            registration_document=SimpleUploadedFile(
                "registration.pdf",
                b"%PDF-1.4\n% test document",
                content_type="application/pdf",
            ),
            is_verified=True,
        )

    def test_registration_rejects_case_insensitive_duplicate_email(self):
        form = RegisterForm(
            data={
                "username": "another_supplier",
                "email": "SUPPLIER@EXAMPLE.COM",
                "phone": "+963900000000",
                "trade_name": "Another Supplier",
                "role": User.Role.SUPPLIER,
                "password1": "StrongTestPass123!",
                "password2": "StrongTestPass123!",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_invalid_document_signature_is_rejected(self):
        form = CompanyProfileForm(
            data={
                "trade_name": self.company.trade_name,
                "business_type": self.company.business_type,
                "city": self.company.city,
                "description": self.company.description,
                "registration_number": self.company.registration_number,
            },
            files={
                "registration_document": SimpleUploadedFile(
                    "fake.pdf",
                    b"this is not a PDF",
                    content_type="application/pdf",
                )
            },
            instance=self.company,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("registration_document", form.errors)

    def test_official_profile_change_resets_verification(self):
        form = CompanyProfileForm(
            data={
                "trade_name": "Supplier With New Legal Name",
                "business_type": self.company.business_type,
                "city": self.company.city,
                "description": self.company.description,
                "registration_number": self.company.registration_number,
            },
            instance=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.company.refresh_from_db()
        self.assertFalse(self.company.is_verified)
        self.assertTrue(form.verification_was_reset)

    def test_stale_profile_form_cannot_restore_revoked_verification(self):
        form = CompanyProfileForm(
            data={
                "trade_name": self.company.trade_name,
                "business_type": "Updated equipment activity",
                "city": self.company.city,
                "description": self.company.description,
                "registration_number": self.company.registration_number,
            },
            instance=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)
        CompanyProfile.objects.filter(pk=self.company.pk).update(is_verified=False)

        form.save()

        self.company.refresh_from_db()
        self.assertFalse(self.company.is_verified)
        self.assertEqual(
            self.company.business_type,
            "Updated equipment activity",
        )

    def test_private_document_is_scoped_to_owner_and_staff(self):
        url = reverse("company_document_download", args=[self.company.pk])
        other = User.objects.create_user(
            username="other_user",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        staff = User.objects.create_user(
            username="document_staff",
            password="TestPass123!",
            role=User.Role.STAFF,
            is_staff=True,
        )

        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)

        for allowed_user in (self.supplier, staff):
            with self.subTest(user=allowed_user.username):
                self.client.force_login(allowed_user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response["Content-Disposition"])
                response.close()

    def test_profile_is_created_for_legacy_account(self):
        legacy_user = User.objects.create_user(
            username="legacy_owner",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        self.client.force_login(legacy_user)
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CompanyProfile.objects.filter(user=legacy_user).exists())
