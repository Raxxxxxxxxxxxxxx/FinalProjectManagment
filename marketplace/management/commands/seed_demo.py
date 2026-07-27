from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import CompanyProfile, User
from marketplace.models import (
    Category,
    Notification,
    OrderStatusEvent,
    Payment,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Quote,
    Review,
    RFQ,
    RFQItem,
    Unit,
)
from marketplace.services import (
    award_quote,
    create_review,
    decide_payment,
    submit_payment,
    transition_order,
)


DEMO_USERNAMES = (
    "owner_demo",
    "supplier_demo",
    "supplier2_demo",
    "admin_demo",
)
DEMO_PASSWORD = "Demo12345!"


class Command(BaseCommand):
    help = "إنشاء بيانات عرض محلية ثابتة وقابلة لإعادة الضبط"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="حذف بيانات حسابات demo فقط وإعادة بنائها من نقطة البداية",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "رفض إنشاء حسابات demo خارج وضع DEBUG لأنها تستخدم كلمة مرور ثابتة."
            )
        if options["reset"]:
            self.reset_demo_data()

        categories = self.seed_categories()
        owner = self.upsert_user(
            "owner_demo",
            User.Role.OWNER,
            "شركة آفاق للنمو",
            "دمشق",
            "owner@example.com",
            "تجارة وتوزيع",
            "شركة سورية نامية تبحث عن مورّدين موثوقين لتوسيع عملياتها.",
            "OWN-DEMO-2026",
        )
        supplier = self.upsert_user(
            "supplier_demo",
            User.Role.SUPPLIER,
            "المتحدون للتجهيزات",
            "ريف دمشق",
            "supplier@example.com",
            "تجهيزات مكتبية وصناعية",
            "مورّد تجهيزات بخبرة في التوريد المؤسسي وخدمات ما بعد البيع.",
            "SUP-DEMO-1001",
        )
        supplier_two = self.upsert_user(
            "supplier2_demo",
            User.Role.SUPPLIER,
            "الريادة لحلول الأعمال",
            "حلب",
            "supplier2@example.com",
            "حلول أعمال وتقنية",
            "شركة متخصصة في الحلول الرقمية والتجهيزات المكتبية.",
            "SUP-DEMO-1002",
        )
        admin = self.upsert_user(
            "admin_demo",
            User.Role.STAFF,
            "إدارة منصة توريد",
            "دمشق",
            "admin@example.com",
            "إدارة منصة",
            "حساب إدارة مخصص للعرض الأكاديمي.",
            "ADM-DEMO-2026",
            is_staff=True,
            is_superuser=True,
        )

        self.seed_products(categories, supplier, supplier_two)
        requests = self.seed_requests(categories, owner)
        self.seed_comparison_case(requests["comparison"], supplier, supplier_two)
        self.seed_shipped_case(requests["shipped"], owner, supplier)
        self.seed_open_case(requests["open"], supplier_two)
        self.seed_completed_case(
            requests["completed"],
            owner,
            supplier_two,
            admin,
        )

        self.stdout.write(self.style.SUCCESS("تم تجهيز بيانات المناقشة بنجاح."))
        self.stdout.write(
            f"الحسابات: {', '.join(DEMO_USERNAMES)} — كلمة المرور: {DEMO_PASSWORD}"
        )
        self.stdout.write(
            "لإعادة السيناريو من نقطة البداية استخدم: python manage.py seed_demo --reset"
        )

    def reset_demo_data(self):
        demo_users = User.objects.filter(username__in=DEMO_USERNAMES)
        demo_rfqs = RFQ.objects.filter(owner__in=demo_users)
        external_supplier_quotes = Quote.objects.filter(
            supplier__in=demo_users
        ).exclude(rfq__owner__in=demo_users)
        external_quotes_on_demo_requests = Quote.objects.filter(
            rfq__owner__in=demo_users
        ).exclude(supplier__in=demo_users)
        if (
            external_supplier_quotes.exists()
            or external_quotes_on_demo_requests.exists()
        ):
            raise CommandError(
                "تعذر reset: توجد معاملات بين حسابات demo وحسابات حقيقية. "
                "افصلها يدوياً أولاً لحماية بيانات المستخدمين."
            )
        demo_orders = PurchaseOrder.objects.filter(rfq__in=demo_rfqs)

        Notification.objects.filter(
            Q(user__in=demo_users)
            | Q(rfq__in=demo_rfqs)
            | Q(order__in=demo_orders)
        ).delete()
        Review.objects.filter(order__in=demo_orders).delete()
        Payment.objects.filter(order__in=demo_orders).delete()
        OrderStatusEvent.objects.filter(order__in=demo_orders).delete()
        PurchaseOrderItem.objects.filter(order__in=demo_orders).delete()
        demo_orders.delete()
        Quote.objects.filter(
            Q(supplier__in=demo_users) | Q(rfq__in=demo_rfqs)
        ).delete()
        RFQItem.objects.filter(rfq__in=demo_rfqs).delete()
        demo_rfqs.delete()
        Product.objects.filter(supplier__in=demo_users).delete()
        demo_profiles = CompanyProfile.objects.filter(user__in=demo_users)
        for profile in demo_profiles:
            if profile.registration_document:
                storage = profile.registration_document.storage
                document_name = profile.registration_document.name
                transaction.on_commit(
                    lambda name=document_name, backend=storage: backend.delete(name)
                )
        demo_profiles.delete()
        demo_users.delete()
        self.stdout.write("تم حذف بيانات حسابات demo السابقة فقط.")

    def seed_categories(self):
        categories_data = [
            ("مواد مكتبية", "Office supplies", "office-supplies"),
            ("تقنية ومعلومات", "Technology and information", "technology"),
            ("تجهيزات ومعدات", "Equipment and supplies", "equipment"),
            ("خدمات مهنية", "Professional services", "professional-services"),
        ]
        categories = {}
        for name, name_en, slug in categories_data:
            category, _ = Category.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "name_en": name_en},
            )
            categories[slug] = category
        return categories

    def seed_products(self, categories, supplier, supplier_two):
        products = [
            (
                supplier,
                categories["office-supplies"],
                "CHAIR-ERG-01",
                "كرسي مكتبي مريح",
                "كرسي بظهر شبكي وقاعدة معدنية مع ضمان سنة.",
                Unit.PIECE,
                Decimal("5"),
                Decimal("145"),
            ),
            (
                supplier,
                categories["equipment"],
                "SAFE-HELMET",
                "خوذة سلامة معتمدة",
                "خوذة صناعية قابلة للضبط ومطابقة لمتطلبات السلامة.",
                Unit.PIECE,
                Decimal("20"),
                Decimal("18.50"),
            ),
            (
                supplier_two,
                categories["technology"],
                "LAP-BIZ-14",
                "حاسوب أعمال محمول",
                "حاسوب محمول فئة أعمال مع ضمان ودعم فني.",
                Unit.PIECE,
                Decimal("3"),
                Decimal("720"),
            ),
            (
                supplier_two,
                categories["professional-services"],
                "WEB-CORP",
                "تصميم وتطوير موقع مؤسسي",
                "تحليل وتصميم وتطوير موقع ثنائي اللغة متجاوب.",
                Unit.PROJECT,
                Decimal("1"),
                Decimal("1600"),
            ),
        ]
        for (
            product_supplier,
            category,
            sku,
            name,
            description,
            unit,
            minimum,
            price,
        ) in products:
            Product.objects.update_or_create(
                supplier=product_supplier,
                name=name,
                defaults={
                    "category": category,
                    "sku": sku,
                    "description": description,
                    "unit": unit,
                    "minimum_order_quantity": minimum,
                    "price": price,
                    "is_active": True,
                },
            )

    def seed_requests(self, categories, owner):
        requests_data = {
            "comparison": {
                "title": "تجهيز مكاتب الفرع الجديد",
                "category": categories["office-supplies"],
                "description": "نبحث عن مورّد لتجهيز 20 محطة عمل مكتبية بجودة مناسبة وضمان توريد واضح.",
                "delivery_city": "دمشق",
                "budget_min": Decimal("4000"),
                "budget_max": Decimal("6500"),
                "items": [
                    ("كرسي مكتبي مريح", 20, Unit.PIECE, "ظهر شبكي ولون أسود"),
                    ("مكتب عمل", 20, Unit.PIECE, "مقاس 140×70 سم"),
                ],
            },
            "shipped": {
                "title": "معدات السلامة المهنية للمستودع",
                "category": categories["equipment"],
                "description": "توريد دفعة معدات سلامة مع شهادات مطابقة وإمكانية التسليم خلال أسبوعين.",
                "delivery_city": "حلب",
                "budget_min": Decimal("1800"),
                "budget_max": Decimal("2800"),
                "items": [
                    ("خوذة سلامة", 60, Unit.PIECE, "مطابقة للمواصفات القياسية"),
                    ("سترة عاكسة", 60, Unit.PIECE, "مقاسات متنوعة"),
                ],
            },
            "open": {
                "title": "تطوير موقع تعريفي للشركة",
                "category": categories["professional-services"],
                "description": "تصميم وتطوير موقع عربي متجاوب من خمس صفحات مع لوحة بسيطة لإدارة المحتوى.",
                "delivery_city": "عن بُعد",
                "budget_min": Decimal("1200"),
                "budget_max": Decimal("2200"),
                "items": [
                    (
                        "تصميم وتطوير الموقع",
                        1,
                        Unit.PROJECT,
                        "عربي RTL، متجاوب، ومحسّن لمحركات البحث",
                    ),
                ],
            },
            "completed": {
                "title": "توريد أجهزة حاسوب لفريق المبيعات",
                "category": categories["technology"],
                "description": "توريد أجهزة فئة أعمال مع نظام تشغيل وضمان ودعم فني.",
                "delivery_city": "دمشق",
                "budget_min": Decimal("3000"),
                "budget_max": Decimal("4500"),
                "items": [
                    (
                        "حاسوب محمول",
                        5,
                        Unit.PIECE,
                        "ذاكرة 16GB وقرص SSD وضمان سنة",
                    ),
                ],
            },
        }
        result = {}
        for index, (key, data) in enumerate(requests_data.items(), start=1):
            items = data["items"]
            defaults = {
                field: value for field, value in data.items() if field != "items"
            }
            defaults.update(
                {
                    "deadline": timezone.localdate() + timedelta(days=7 + index),
                    "status": RFQ.Status.OPEN,
                }
            )
            rfq, created = RFQ.objects.get_or_create(
                owner=owner,
                title=data["title"],
                defaults=defaults,
            )
            if created:
                RFQItem.objects.bulk_create(
                    [
                        RFQItem(
                            rfq=rfq,
                            name=name,
                            quantity=quantity,
                            unit=unit,
                            specifications=specifications,
                        )
                        for name, quantity, unit, specifications in items
                    ]
                )
            result[key] = rfq
        return result

    def seed_comparison_case(self, rfq, supplier, supplier_two):
        Quote.objects.get_or_create(
            rfq=rfq,
            supplier=supplier,
            defaults={
                "total_amount": Decimal("4850"),
                "delivery_days": 9,
                "notes": "السعر يشمل النقل والتركيب وضمان سنة.",
            },
        )
        Quote.objects.get_or_create(
            rfq=rfq,
            supplier=supplier_two,
            defaults={
                "total_amount": Decimal("4590"),
                "delivery_days": 12,
                "notes": "عرض اقتصادي يشمل النقل وضمان ستة أشهر.",
            },
        )

    def seed_shipped_case(self, rfq, owner, supplier):
        quote, _ = Quote.objects.get_or_create(
            rfq=rfq,
            supplier=supplier,
            defaults={
                "total_amount": Decimal("2350"),
                "delivery_days": 8,
                "notes": "جميع المواد مطابقة للمواصفات مع شهادات منشأ.",
            },
        )
        if PurchaseOrder.objects.filter(rfq=rfq).exists():
            return
        order, _ = award_quote(quote_id=quote.pk, actor=owner)
        transition_order(
            order_id=order.pk,
            actor=supplier,
            target_status=PurchaseOrder.Status.CONFIRMED,
            note="تم تأكيد توافر المواد.",
        )
        submit_payment(
            order_id=order.pk,
            actor=owner,
            method=Payment.Method.CASH_ON_DELIVERY,
            note="الدفع عند الاستلام.",
        )
        transition_order(
            order_id=order.pk,
            actor=supplier,
            target_status=PurchaseOrder.Status.PREPARING,
            note="بدأ تجهيز الطلب.",
        )
        transition_order(
            order_id=order.pk,
            actor=supplier,
            target_status=PurchaseOrder.Status.SHIPPED,
            note="تم تسليم الشحنة إلى شركة النقل.",
            tracking_reference="SHAM-DEMO-2048",
        )

    def seed_open_case(self, rfq, supplier):
        Quote.objects.get_or_create(
            rfq=rfq,
            supplier=supplier,
            defaults={
                "total_amount": Decimal("1750"),
                "delivery_days": 15,
                "notes": "يشمل التصميم والتطوير والتدريب.",
            },
        )

    def seed_completed_case(self, rfq, owner, supplier, admin):
        quote, _ = Quote.objects.get_or_create(
            rfq=rfq,
            supplier=supplier,
            defaults={
                "total_amount": Decimal("3650"),
                "delivery_days": 6,
                "notes": "أجهزة أعمال مع ضمان ودعم فني لمدة سنة.",
            },
        )
        if PurchaseOrder.objects.filter(rfq=rfq).exists():
            return
        order, _ = award_quote(quote_id=quote.pk, actor=owner)
        transition_order(
            order_id=order.pk,
            actor=supplier,
            target_status=PurchaseOrder.Status.CONFIRMED,
            note="تم تأكيد المخزون.",
        )
        payment = submit_payment(
            order_id=order.pk,
            actor=owner,
            method=Payment.Method.BANK_TRANSFER,
            reference="DEMO-BANK-2026",
            note="تحويل تجريبي موثق.",
        )
        decide_payment(
            payment_id=payment.pk,
            actor=admin,
            decision="confirm",
            admin_note="تمت المطابقة لأغراض العرض.",
        )
        for target, note in (
            (PurchaseOrder.Status.PREPARING, "بدأ تجهيز الأجهزة."),
            (PurchaseOrder.Status.SHIPPED, "أرسلت الشحنة."),
            (PurchaseOrder.Status.DELIVERED, "وصلت الشحنة إلى العميل."),
        ):
            transition_order(
                order_id=order.pk,
                actor=supplier,
                target_status=target,
                note=note,
                tracking_reference=(
                    "SHAM-COMPLETE-2026"
                    if target == PurchaseOrder.Status.SHIPPED
                    else ""
                ),
            )
        transition_order(
            order_id=order.pk,
            actor=owner,
            target_status=PurchaseOrder.Status.COMPLETED,
            note="تم فحص الأجهزة وتأكيد الاستلام.",
        )
        create_review(
            order_id=order.pk,
            actor=owner,
            rating=5,
            comment="توريد منظم وجودة ممتازة والتزام بالموعد.",
        )

    def upsert_user(
        self,
        username,
        role,
        trade_name,
        city,
        email,
        business_type,
        description,
        registration_number,
        is_staff=False,
        is_superuser=False,
    ):
        user, _ = User.objects.get_or_create(username=username)
        user.role = role
        user.email = email
        user.phone = "+963 900 000 000"
        user.email_verified = True
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.is_active = True
        user.set_password(DEMO_PASSWORD)
        user.save()
        CompanyProfile.objects.update_or_create(
            user=user,
            defaults={
                "trade_name": trade_name,
                "business_type": business_type,
                "city": city,
                "description": description,
                "registration_number": registration_number,
                "is_verified": True,
            },
        )
        return user
