from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from accounts.models import CompanyProfile, User

from .models import Category, Quote, RFQ, RFQItem


class MarketplaceFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        self.supplier = User.objects.create_user(
            username="supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        CompanyProfile.objects.create(
            user=self.supplier,
            trade_name="Test Supplier",
            is_verified=True,
        )
        self.category = Category.objects.create(name="تقنية", slug="tech")
        self.rfq = RFQ.objects.create(
            owner=self.owner,
            category=self.category,
            title="أجهزة حاسوب",
            description="مطلوب أجهزة لفريق العمل",
            delivery_city="دمشق",
            deadline=timezone.localdate() + timedelta(days=7),
        )
        RFQItem.objects.create(
            rfq=self.rfq,
            name="حاسوب محمول",
            quantity=5,
            unit="قطعة",
        )

    def test_public_can_browse_open_requests(self):
        response = self.client.get(reverse("rfq_list"))
        self.assertContains(response, self.rfq.title)

    def test_supplier_cannot_create_rfq(self):
        self.client.force_login(self.supplier)
        response = self.client.get(reverse("rfq_create"))
        self.assertEqual(response.status_code, 403)

    def test_owner_can_create_rfq_with_item(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("rfq_create"),
            {
                "title": "طابعات مكتبية",
                "category": self.category.pk,
                "description": "مطلوب طابعات ليزر",
                "delivery_city": "حمص",
                "deadline": (timezone.localdate() + timedelta(days=10)).isoformat(),
                "budget_min": "500",
                "budget_max": "900",
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "0",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "1000",
                "items-0-name": "طابعة",
                "items-0-quantity": "3",
                "items-0-unit": "piece",
                "items-0-specifications": "ليزر",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = RFQ.objects.get(title="طابعات مكتبية")
        self.assertEqual(created.items.count(), 1)

    def test_supplier_can_submit_only_one_quote(self):
        self.client.force_login(self.supplier)
        url = reverse("quote_create", args=[self.rfq.pk])
        response = self.client.post(
            url,
            {"total_amount": "700", "delivery_days": "4", "notes": "عرض شامل"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Quote.objects.filter(rfq=self.rfq, supplier=self.supplier).count(), 1)
        second_response = self.client.post(
            url,
            {"total_amount": "650", "delivery_days": "3", "notes": ""},
        )
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Quote.objects.filter(rfq=self.rfq, supplier=self.supplier).count(), 1)


class InternationalizationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name="تقنية",
            name_en="Technology",
            slug="technology",
        )

    def test_arabic_is_rtl_and_uses_translated_interface(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, '<html lang="ar" dir="rtl">', html=False)
        self.assertContains(response, "منصة أكاديمية للتوريد الذكي")

    def test_language_switch_persists_english_and_ltr(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "en", "next": reverse("home")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies["django_language"].value, "en")

        home_response = self.client.get(reverse("home"))
        self.assertContains(home_response, '<html lang="en" dir="ltr">', html=False)
        self.assertContains(home_response, "An academic platform for intelligent procurement")

    def test_category_uses_name_for_active_language(self):
        with override("ar"):
            self.assertEqual(self.category.display_name, "تقنية")
        with override("en"):
            self.assertEqual(self.category.display_name, "Technology")

    def test_base_exposes_theme_controls(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'id="themeToggle"', html=False)
        self.assertContains(response, "sham-theme")
