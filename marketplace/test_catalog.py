from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyProfile, User

from .models import Category, Product, Quote, RFQ, RFQItem, Unit
from .views import _csv_safe


class CatalogAndQuoteManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="catalog_owner",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        cls.supplier = User.objects.create_user(
            username="catalog_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        cls.other_supplier = User.objects.create_user(
            username="other_catalog_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        cls.unverified_supplier = User.objects.create_user(
            username="unverified_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        CompanyProfile.objects.create(
            user=cls.supplier,
            trade_name="Verified Catalog Supplier",
            is_verified=True,
        )
        CompanyProfile.objects.create(
            user=cls.other_supplier,
            trade_name="Other Verified Supplier",
            is_verified=True,
        )
        CompanyProfile.objects.create(
            user=cls.unverified_supplier,
            trade_name="Hidden Supplier",
            is_verified=False,
        )
        cls.category = Category.objects.create(
            name="معدات الاختبار",
            name_en="Test equipment",
            slug="catalog-test-equipment",
        )
        cls.product = Product.objects.create(
            supplier=cls.supplier,
            category=cls.category,
            sku="TEST-001",
            name="Visible product",
            description="A catalog item from a verified supplier.",
            unit=Unit.PIECE,
            minimum_order_quantity=Decimal("2"),
            price=Decimal("125.50"),
        )
        cls.hidden_product = Product.objects.create(
            supplier=cls.unverified_supplier,
            category=cls.category,
            name="Hidden unverified product",
            unit=Unit.PIECE,
            price=Decimal("50"),
        )
        cls.rfq = RFQ.objects.create(
            owner=cls.owner,
            category=cls.category,
            title="Catalog workflow RFQ",
            description="RFQ used to test quote management.",
            delivery_city="Damascus",
            deadline=timezone.localdate() + timedelta(days=7),
        )
        RFQItem.objects.create(
            rfq=cls.rfq,
            name="Test item",
            quantity=2,
            unit=Unit.PIECE,
        )

    def test_public_catalog_exposes_only_verified_supplier_products(self):
        response = self.client.get(reverse("product_list"))
        self.assertContains(response, self.product.name)
        self.assertNotContains(response, self.hidden_product.name)
        self.assertEqual(
            self.client.get(
                reverse("product_detail", args=[self.hidden_product.pk])
            ).status_code,
            404,
        )

    def test_supplier_can_create_and_only_edit_own_product(self):
        self.client.force_login(self.supplier)
        response = self.client.post(
            reverse("product_create"),
            {
                "category": self.category.pk,
                "sku": "NEW-002",
                "name": "New supplier service",
                "description": "Created through the supplier workspace.",
                "unit": Unit.SERVICE,
                "minimum_order_quantity": "1",
                "price": "300",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = Product.objects.get(
            supplier=self.supplier,
            name="New supplier service",
        )

        self.client.force_login(self.other_supplier)
        self.assertEqual(
            self.client.get(reverse("product_edit", args=[created.pk])).status_code,
            404,
        )

    def test_unverified_supplier_cannot_submit_quote(self):
        self.client.force_login(self.unverified_supplier)
        response = self.client.post(
            reverse("quote_create", args=[self.rfq.pk]),
            {
                "total_amount": "800",
                "delivery_days": "4",
                "notes": "Must not be stored.",
            },
        )
        self.assertRedirects(response, reverse("profile"))
        self.assertFalse(
            Quote.objects.filter(
                rfq=self.rfq,
                supplier=self.unverified_supplier,
            ).exists()
        )

    def test_supplier_can_edit_pending_quote_before_deadline(self):
        quote = Quote.objects.create(
            rfq=self.rfq,
            supplier=self.supplier,
            total_amount=Decimal("900"),
            delivery_days=5,
        )
        self.client.force_login(self.supplier)
        response = self.client.post(
            reverse("quote_edit", args=[quote.pk]),
            {
                "total_amount": "825.25",
                "delivery_days": "3",
                "notes": "Updated commercial proposal.",
            },
        )
        self.assertRedirects(response, reverse("my_quotes"))
        quote.refresh_from_db()
        self.assertEqual(quote.total_amount, Decimal("825.25"))
        self.assertEqual(quote.delivery_days, 3)

    def test_quote_withdrawal_requires_post_and_ownership(self):
        quote = Quote.objects.create(
            rfq=self.rfq,
            supplier=self.supplier,
            total_amount=Decimal("900"),
            delivery_days=5,
        )
        url = reverse("quote_withdraw", args=[quote.pk])

        self.client.force_login(self.supplier)
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.force_login(self.other_supplier)
        self.assertEqual(self.client.post(url).status_code, 404)

        self.client.force_login(self.supplier)
        self.assertEqual(self.client.post(url).status_code, 302)
        quote.refresh_from_db()
        self.assertEqual(quote.status, Quote.Status.WITHDRAWN)

    def test_comparison_displays_transparent_decision_score(self):
        Quote.objects.create(
            rfq=self.rfq,
            supplier=self.supplier,
            total_amount=Decimal("900"),
            delivery_days=5,
        )
        Quote.objects.create(
            rfq=self.rfq,
            supplier=self.other_supplier,
            total_amount=Decimal("850"),
            delivery_days=7,
        )
        self.client.force_login(self.owner)
        response = self.client.get(reverse("rfq_compare", args=[self.rfq.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100")
        self.assertContains(response, "50%")

    def test_comparison_handles_quotes_when_all_suppliers_become_unverified(self):
        Quote.objects.create(
            rfq=self.rfq,
            supplier=self.supplier,
            total_amount=Decimal("900"),
            delivery_days=5,
        )
        Quote.objects.create(
            rfq=self.rfq,
            supplier=self.other_supplier,
            total_amount=Decimal("850"),
            delivery_days=7,
        )
        CompanyProfile.objects.filter(
            user__in=(self.supplier, self.other_supplier)
        ).update(is_verified=False)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("rfq_compare", args=[self.rfq.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["recommended_quote"])
        self.assertTrue(
            all(not quote.is_eligible for quote in response.context["quotes"])
        )

    def test_security_headers_and_local_tailwind_are_served(self):
        response = self.client.get(reverse("home"))
        self.assertIn("Content-Security-Policy", response)
        self.assertContains(response, "css/tailwind.css")
        self.assertNotContains(response, "cdn.tailwindcss.com")

    def test_report_csv_export_requires_login_and_is_downloadable(self):
        url = reverse("reports_export_csv")
        anonymous_response = self.client.get(url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(reverse("login"), anonymous_response.url)

        self.client.force_login(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        for dangerous_value in (
            "=1+1",
            "+SUM(A1:A2)",
            "-2+3",
            "@command",
            "  =HYPERLINK(\"bad\")",
            "\t=1+1",
            "\r=1+1",
            "\n=1+1",
        ):
            with self.subTest(value=dangerous_value):
                self.assertTrue(_csv_safe(dangerous_value).startswith("'"))

    def test_health_endpoint_checks_database(self):
        response = self.client.get(reverse("health_check"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(DEBUG=False)
    def test_demo_seed_is_blocked_outside_debug_mode(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo")
