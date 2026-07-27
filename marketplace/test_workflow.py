from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import CompanyProfile, User

from .models import (
    Category,
    Notification,
    OrderStatusEvent,
    Payment,
    PurchaseOrder,
    Quote,
    Review,
    RFQ,
)
from .services import WorkflowConflict, award_quote, decide_payment, transition_order


class WorkflowTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="workflow_owner",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        cls.other_owner = User.objects.create_user(
            username="workflow_other_owner",
            password="TestPass123!",
            role=User.Role.OWNER,
        )
        cls.supplier = User.objects.create_user(
            username="workflow_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        cls.other_supplier = User.objects.create_user(
            username="workflow_other_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        cls.outside_supplier = User.objects.create_user(
            username="workflow_outside_supplier",
            password="TestPass123!",
            role=User.Role.SUPPLIER,
        )
        cls.staff = User.objects.create_user(
            username="workflow_staff",
            password="TestPass123!",
            role=User.Role.STAFF,
            is_staff=True,
        )
        for supplier in (
            cls.supplier,
            cls.other_supplier,
            cls.outside_supplier,
        ):
            CompanyProfile.objects.create(
                user=supplier,
                trade_name=supplier.username,
                is_verified=True,
            )
        cls.category = Category.objects.create(
            name="اختبارات سير العمل",
            name_en="Workflow tests",
            slug="workflow-tests",
        )
        cls.rfq = RFQ.objects.create(
            owner=cls.owner,
            category=cls.category,
            title="توريد تجهيزات مخبرية",
            description="طلب مخصص لاختبارات دورة التوريد",
            delivery_city="دمشق",
            deadline=timezone.localdate() + timedelta(days=7),
            status=RFQ.Status.OPEN,
        )
        cls.quote = Quote.objects.create(
            rfq=cls.rfq,
            supplier=cls.supplier,
            total_amount=Decimal("1250.50"),
            delivery_days=5,
            notes="العرض المرشح",
        )
        cls.competing_quote = Quote.objects.create(
            rfq=cls.rfq,
            supplier=cls.other_supplier,
            total_amount=Decimal("1400.00"),
            delivery_days=3,
            notes="عرض منافس",
        )

    def award(self):
        order, created = award_quote(quote_id=self.quote.pk, actor=self.owner)
        self.assertTrue(created)
        return order

    def confirm_order_as_supplier(self, order):
        self.client.force_login(self.supplier)
        response = self.client.post(
            reverse("order_update_status", args=[order.pk]),
            {
                "target_status": PurchaseOrder.Status.CONFIRMED,
                "note": "تم تأكيد الطلب",
            },
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)


class QuoteAwardTests(WorkflowTestBase):
    def test_owner_awards_quote_and_creates_consistent_purchase_order(self):
        self.client.force_login(self.owner)

        response = self.client.post(reverse("quote_award", args=[self.quote.pk]))

        self.assertEqual(response.status_code, 302)
        self.rfq.refresh_from_db()
        self.quote.refresh_from_db()
        self.competing_quote.refresh_from_db()
        order = PurchaseOrder.objects.get(rfq=self.rfq)
        self.assertEqual(self.rfq.status, RFQ.Status.AWARDED)
        self.assertEqual(self.quote.status, Quote.Status.SELECTED)
        self.assertEqual(self.competing_quote.status, Quote.Status.REJECTED)
        self.assertEqual(order.quote, self.quote)
        self.assertEqual(order.agreed_amount, self.quote.total_amount)
        self.assertEqual(order.delivery_days, self.quote.delivery_days)
        self.assertEqual(
            order.status,
            PurchaseOrder.Status.AWAITING_CONFIRMATION,
        )
        self.assertTrue(
            OrderStatusEvent.objects.filter(
                order=order,
                actor=self.owner,
                from_status="",
                to_status=PurchaseOrder.Status.AWAITING_CONFIRMATION,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.supplier,
                kind=Notification.Kind.QUOTE_SELECTED,
                order=order,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.other_supplier,
                kind=Notification.Kind.QUOTE_REJECTED,
                order__isnull=True,
                rfq__isnull=True,
            ).exists()
        )

    def test_awarding_same_quote_is_idempotent(self):
        self.client.force_login(self.owner)
        url = reverse("quote_award", args=[self.quote.pk])

        first_response = self.client.post(url)
        second_response = self.client.post(url)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(PurchaseOrder.objects.filter(rfq=self.rfq).count(), 1)
        self.assertEqual(
            Quote.objects.filter(rfq=self.rfq, status=Quote.Status.SELECTED).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                user=self.supplier,
                kind=Notification.Kind.QUOTE_SELECTED,
                rfq=self.rfq,
            ).count(),
            1,
        )

    def test_second_quote_cannot_replace_awarded_quote(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("quote_award", args=[self.quote.pk]))

        response = self.client.post(
            reverse("quote_award", args=[self.competing_quote.pk])
        )

        self.assertEqual(response.status_code, 302)
        order = PurchaseOrder.objects.get(rfq=self.rfq)
        self.assertEqual(order.quote, self.quote)
        self.assertEqual(PurchaseOrder.objects.filter(rfq=self.rfq).count(), 1)
        self.quote.refresh_from_db()
        self.competing_quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.Status.SELECTED)
        self.assertEqual(self.competing_quote.status, Quote.Status.REJECTED)

    def test_only_request_owner_can_award_quote(self):
        url = reverse("quote_award", args=[self.quote.pk])
        for actor in (
            self.other_owner,
            self.supplier,
            self.other_supplier,
            self.staff,
        ):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.post(url)
                self.assertEqual(response.status_code, 404)

        self.assertFalse(PurchaseOrder.objects.filter(rfq=self.rfq).exists())
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, RFQ.Status.OPEN)

    def test_quote_from_unverified_supplier_cannot_be_awarded(self):
        CompanyProfile.objects.filter(user=self.supplier).update(is_verified=False)
        self.client.force_login(self.owner)

        response = self.client.post(reverse("quote_award", args=[self.quote.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PurchaseOrder.objects.filter(rfq=self.rfq).exists())
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, RFQ.Status.OPEN)

    def test_quote_from_inactive_supplier_cannot_be_awarded(self):
        User.objects.filter(pk=self.supplier.pk).update(is_active=False)
        self.client.force_login(self.owner)

        response = self.client.post(reverse("quote_award", args=[self.quote.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PurchaseOrder.objects.filter(rfq=self.rfq).exists())
        self.rfq.refresh_from_db()
        self.assertEqual(self.rfq.status, RFQ.Status.OPEN)

    def test_award_requires_login_post_and_csrf(self):
        url = reverse("quote_award", args=[self.quote.pk])
        anonymous_response = self.client.post(url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(reverse("login"), anonymous_response.url)

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertFalse(PurchaseOrder.objects.filter(rfq=self.rfq).exists())


class QuoteSubmissionTests(WorkflowTestBase):
    def test_supplier_cannot_submit_quote_after_deadline(self):
        expired_rfq = RFQ.objects.create(
            owner=self.owner,
            category=self.category,
            title="طلب منتهي",
            description="انتهت مهلة هذا الطلب",
            delivery_city="دمشق",
            deadline=timezone.localdate() - timedelta(days=1),
            status=RFQ.Status.OPEN,
        )
        self.client.force_login(self.outside_supplier)
        url = reverse("quote_create", args=[expired_rfq.pk])

        get_response = self.client.get(url)
        post_response = self.client.post(
            url,
            {
                "total_amount": "900.00",
                "delivery_days": "2",
                "notes": "يجب ألا يحفظ",
            },
        )

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        self.assertFalse(
            Quote.objects.filter(
                rfq=expired_rfq,
                supplier=self.outside_supplier,
            ).exists()
        )

    def test_deadline_today_accepts_quote_and_notifies_only_owner(self):
        today_rfq = RFQ.objects.create(
            owner=self.owner,
            category=self.category,
            title="طلب ينتهي اليوم",
            description="ما يزال مفتوحاً اليوم",
            delivery_city="دمشق",
            deadline=timezone.localdate(),
            status=RFQ.Status.OPEN,
        )
        self.client.force_login(self.outside_supplier)

        response = self.client.post(
            reverse("quote_create", args=[today_rfq.pk]),
            {
                "total_amount": "980.00",
                "delivery_days": "4",
                "notes": "عرض صالح",
            },
        )

        self.assertEqual(response.status_code, 302)
        quote = Quote.objects.get(
            rfq=today_rfq,
            supplier=self.outside_supplier,
        )
        notification = Notification.objects.get(
            kind=Notification.Kind.QUOTE_RECEIVED,
            rfq=today_rfq,
        )
        self.assertEqual(notification.user, self.owner)
        self.assertEqual(notification.actor, self.outside_supplier)
        self.assertIn(str(quote.pk), notification.event_key)
        self.assertFalse(
            Notification.objects.filter(
                user=self.outside_supplier,
                kind=Notification.Kind.QUOTE_RECEIVED,
                rfq=today_rfq,
            ).exists()
        )


class OrderPermissionAndTransitionTests(WorkflowTestBase):
    def test_order_detail_is_scoped_to_parties_and_staff(self):
        order = self.award()
        url = reverse("order_detail", args=[order.pk])

        for actor in (self.owner, self.supplier, self.staff):
            with self.subTest(allowed=actor.username):
                self.client.force_login(actor)
                self.assertEqual(self.client.get(url).status_code, 200)

        for actor in (
            self.other_owner,
            self.other_supplier,
            self.outside_supplier,
        ):
            with self.subTest(forbidden=actor.username):
                self.client.force_login(actor)
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_owner_cannot_perform_supplier_confirmation(self):
        order = self.award()
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("order_update_status", args=[order.pk]),
            {"target_status": PurchaseOrder.Status.CONFIRMED, "note": ""},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(
            order.status,
            PurchaseOrder.Status.AWAITING_CONFIRMATION,
        )
        self.assertEqual(order.status_events.count(), 1)

    def test_preparation_is_blocked_until_payment_allows_fulfillment(self):
        order = self.award()
        self.confirm_order_as_supplier(order)

        response = self.client.post(
            reverse("order_update_status", args=[order.pk]),
            {"target_status": PurchaseOrder.Status.PREPARING, "note": ""},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertFalse(
            order.status_events.filter(
                to_status=PurchaseOrder.Status.PREPARING
            ).exists()
        )

    def test_full_cash_on_delivery_transition_flow(self):
        order = self.award()
        self.confirm_order_as_supplier(order)

        self.client.force_login(self.owner)
        payment_response = self.client.post(
            reverse("payment_submit", args=[order.pk]),
            {
                "method": Payment.Method.CASH_ON_DELIVERY,
                "reference": "",
                "note": "الدفع عند الاستلام",
            },
        )
        self.assertEqual(payment_response.status_code, 302)
        payment = Payment.objects.get(order=order)
        self.assertEqual(payment.status, Payment.Status.SUBMITTED)

        self.client.force_login(self.supplier)
        for target_status, payload in (
            (PurchaseOrder.Status.PREPARING, {"note": "بدء التجهيز"}),
            (
                PurchaseOrder.Status.SHIPPED,
                {
                    "note": "غادر المستودع",
                    "tracking_reference": "TRACK-123",
                },
            ),
            (PurchaseOrder.Status.DELIVERED, {"note": "تم التسليم"}),
        ):
            with self.subTest(target_status=target_status):
                response = self.client.post(
                    reverse("order_update_status", args=[order.pk]),
                    {"target_status": target_status, **payload},
                )
                self.assertEqual(response.status_code, 302)
                order.refresh_from_db()
                self.assertEqual(order.status, target_status)

        self.assertEqual(order.tracking_reference, "TRACK-123")

        self.client.force_login(self.supplier)
        supplier_completion = self.client.post(
            reverse("order_update_status", args=[order.pk]),
            {"target_status": PurchaseOrder.Status.COMPLETED, "note": ""},
        )
        self.assertEqual(supplier_completion.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DELIVERED)

        self.client.force_login(self.owner)
        owner_completion = self.client.post(
            reverse("order_update_status", args=[order.pk]),
            {
                "target_status": PurchaseOrder.Status.COMPLETED,
                "note": "تأكيد الاستلام",
            },
        )
        self.assertEqual(owner_completion.status_code, 302)
        order.refresh_from_db()
        self.rfq.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.COMPLETED)
        self.assertIsNotNone(order.completed_at)
        self.assertEqual(self.rfq.status, RFQ.Status.CLOSED)
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.owner)
        self.assertEqual(order.status_events.count(), 6)
        order_notifications = Notification.objects.filter(
            kind=Notification.Kind.ORDER_STATUS,
            order=order,
        )
        self.assertEqual(order_notifications.count(), 5)
        self.assertEqual(order_notifications.filter(user=self.owner).count(), 4)
        self.assertEqual(order_notifications.filter(user=self.supplier).count(), 1)

    def test_order_status_endpoint_rejects_get(self):
        order = self.award()
        self.client.force_login(self.supplier)
        response = self.client.get(reverse("order_update_status", args=[order.pk]))
        self.assertEqual(response.status_code, 405)


class ManualPaymentTests(WorkflowTestBase):
    def create_confirmed_order(self):
        order = self.award()
        self.confirm_order_as_supplier(order)
        return order

    def submit_bank_payment(self, order, reference="BANK-REF-100"):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("payment_submit", args=[order.pk]),
            {
                "method": Payment.Method.BANK_TRANSFER,
                "reference": reference,
                "note": "تم التحويل",
            },
        )
        self.assertEqual(response.status_code, 302)
        return Payment.objects.get(order=order)

    def test_bank_payment_requires_reference_and_uses_order_amount(self):
        order = self.create_confirmed_order()
        self.client.force_login(self.owner)

        missing_reference_response = self.client.post(
            reverse("payment_submit", args=[order.pk]),
            {
                "method": Payment.Method.BANK_TRANSFER,
                "reference": " ",
                "note": "",
            },
        )

        self.assertEqual(missing_reference_response.status_code, 200)
        self.assertFalse(Payment.objects.filter(order=order).exists())

        payment = self.submit_bank_payment(order)
        self.assertEqual(payment.amount, order.agreed_amount)
        self.assertEqual(payment.status, Payment.Status.SUBMITTED)
        self.assertEqual(payment.submission_count, 1)
        self.assertIsNotNone(payment.submitted_at)
        self.assertTrue(
            Notification.objects.filter(
                user=self.staff,
                kind=Notification.Kind.PAYMENT_SUBMITTED,
                order=order,
            ).exists()
        )

    def test_only_staff_can_confirm_payment_and_repeat_is_idempotent(self):
        order = self.create_confirmed_order()
        payment = self.submit_bank_payment(order)
        review_url = reverse("payment_review", args=[payment.pk])

        for actor in (self.owner, self.other_owner, self.supplier):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                self.assertEqual(
                    self.client.post(
                        review_url,
                        {"decision": "confirm", "admin_note": ""},
                    ).status_code,
                    404,
                )

        self.client.force_login(self.staff)
        first_response = self.client.post(
            review_url,
            {"decision": "confirm", "admin_note": "مطابق"},
        )
        second_response = self.client.post(
            review_url,
            {"decision": "confirm", "admin_note": "إعادة الطلب"},
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.staff)
        self.assertEqual(
            Notification.objects.filter(
                kind=Notification.Kind.PAYMENT_CONFIRMED,
                order=order,
            ).count(),
            2,
        )
        self.assertSetEqual(
            set(
                Notification.objects.filter(
                    kind=Notification.Kind.PAYMENT_CONFIRMED,
                    order=order,
                ).values_list("user_id", flat=True)
            ),
            {self.owner.pk, self.supplier.pk},
        )

    def test_rejected_payment_can_be_resubmitted(self):
        order = self.create_confirmed_order()
        payment = self.submit_bank_payment(order, reference="FIRST-REF")
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("payment_review", args=[payment.pk]),
            {
                "decision": "reject",
                "admin_note": "المرجع غير واضح",
            },
        )
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REJECTED)

        payment = self.submit_bank_payment(order, reference="SECOND-REF")
        self.assertEqual(payment.status, Payment.Status.SUBMITTED)
        self.assertEqual(payment.reference, "SECOND-REF")
        self.assertEqual(payment.submission_count, 2)

    def test_cash_on_delivery_cannot_be_rejected_after_preparation_starts(self):
        order = self.create_confirmed_order()
        self.client.force_login(self.owner)
        self.client.post(
            reverse("payment_submit", args=[order.pk]),
            {
                "method": Payment.Method.CASH_ON_DELIVERY,
                "reference": "",
                "note": "الدفع عند الاستلام",
            },
        )
        payment = Payment.objects.get(order=order)

        transition_order(
            order_id=order.pk,
            actor=self.supplier,
            target_status=PurchaseOrder.Status.PREPARING,
        )
        with self.assertRaises(WorkflowConflict):
            decide_payment(
                payment_id=payment.pk,
                actor=self.staff,
                decision="reject",
                admin_note="قرار متأخر",
            )

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.SUBMITTED)

        for actor, target_status in (
            (self.supplier, PurchaseOrder.Status.SHIPPED),
            (self.supplier, PurchaseOrder.Status.DELIVERED),
            (self.owner, PurchaseOrder.Status.COMPLETED),
        ):
            transition_order(
                order_id=order.pk,
                actor=actor,
                target_status=target_status,
            )

        order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.COMPLETED)
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.owner)

    def test_payment_and_review_routes_hide_other_owners_orders(self):
        order = self.create_confirmed_order()
        self.client.force_login(self.other_owner)

        payment_response = self.client.get(
            reverse("payment_submit", args=[order.pk])
        )
        review_response = self.client.get(reverse("review_create", args=[order.pk]))

        self.assertEqual(payment_response.status_code, 404)
        self.assertEqual(review_response.status_code, 404)


class ReviewWorkflowTests(WorkflowTestBase):
    def test_review_is_blocked_before_completion(self):
        order = self.award()
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("review_create", args=[order.pk]),
            {"rating": "5", "comment": "مبكر"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.filter(order=order).exists())

    def test_owner_reviews_completed_order_once_and_supplier_is_notified(self):
        order = self.award()
        PurchaseOrder.objects.filter(pk=order.pk).update(
            status=PurchaseOrder.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        self.client.force_login(self.owner)
        url = reverse("review_create", args=[order.pk])

        first_response = self.client.post(
            url,
            {"rating": "5", "comment": "  تنفيذ ممتاز  "},
        )
        second_response = self.client.post(
            url,
            {"rating": "4", "comment": "يجب ألا ينشأ تقييم ثانٍ"},
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(Review.objects.filter(order=order).count(), 1)
        review = Review.objects.get(order=order)
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.comment, "تنفيذ ممتاز")
        self.assertEqual(
            Notification.objects.filter(
                user=self.supplier,
                kind=Notification.Kind.REVIEW_RECEIVED,
                order=order,
            ).count(),
            1,
        )

    def test_non_owner_cannot_review_completed_order(self):
        order = self.award()
        PurchaseOrder.objects.filter(pk=order.pk).update(
            status=PurchaseOrder.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        for actor in (self.supplier, self.other_owner, self.other_supplier):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                response = self.client.post(
                    reverse("review_create", args=[order.pk]),
                    {"rating": "5", "comment": "غير مصرح"},
                )
                self.assertEqual(response.status_code, 404)
        self.assertFalse(Review.objects.filter(order=order).exists())


class NotificationPermissionTests(WorkflowTestBase):
    def test_status_notifications_keep_the_status_recorded_at_creation(self):
        order = self.award()
        self.confirm_order_as_supplier(order)

        self.client.force_login(self.owner)
        self.client.post(
            reverse("payment_submit", args=[order.pk]),
            {
                "method": Payment.Method.CASH_ON_DELIVERY,
                "reference": "",
                "note": "",
            },
        )
        transition_order(
            order_id=order.pk,
            actor=self.supplier,
            target_status=PurchaseOrder.Status.PREPARING,
        )

        notifications = Notification.objects.filter(
            user=self.owner,
            kind=Notification.Kind.ORDER_STATUS,
            order=order,
        )
        by_status = {
            notification.payload["status"]: notification
            for notification in notifications
        }
        order.refresh_from_db()

        self.assertEqual(order.status, PurchaseOrder.Status.PREPARING)
        self.assertEqual(
            by_status[PurchaseOrder.Status.CONFIRMED].payload["status"],
            PurchaseOrder.Status.CONFIRMED,
        )
        self.assertIn(
            str(PurchaseOrder.Status.CONFIRMED.label),
            by_status[PurchaseOrder.Status.CONFIRMED].message,
        )
        self.assertIn(
            str(PurchaseOrder.Status.PREPARING.label),
            by_status[PurchaseOrder.Status.PREPARING].message,
        )

        confirmed_notification = by_status[PurchaseOrder.Status.CONFIRMED]
        Notification.objects.filter(pk=confirmed_notification.pk).update(payload={})
        confirmed_notification.refresh_from_db()
        self.assertIn(
            str(PurchaseOrder.Status.CONFIRMED.label),
            confirmed_notification.message,
        )

    def test_rejected_supplier_can_open_notification_without_private_order_access(self):
        self.award()
        notification = Notification.objects.get(
            user=self.other_supplier,
            kind=Notification.Kind.QUOTE_REJECTED,
        )
        self.assertIsNone(notification.order_id)
        self.assertIsNone(notification.rfq_id)
        self.assertEqual(notification.payload["request_title"], self.rfq.title)
        self.assertIn(self.rfq.title, notification.message)

        self.client.force_login(self.other_supplier)
        response = self.client.post(
            reverse("notification_open", args=[notification.pk])
        )

        self.assertRedirects(response, reverse("notification_list"))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_notification_open_is_owner_scoped_and_post_only(self):
        notification = Notification.objects.create(
            user=self.owner,
            actor=self.supplier,
            kind=Notification.Kind.QUOTE_RECEIVED,
            event_key="workflow-test-private-notification",
            rfq=self.rfq,
        )
        url = reverse("notification_open", args=[notification.pk])

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.force_login(self.other_owner)
        self.assertEqual(self.client.post(url).status_code, 404)
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)

        self.client.force_login(self.owner)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read_does_not_touch_another_users_notifications(self):
        owner_notification = Notification.objects.create(
            user=self.owner,
            kind=Notification.Kind.QUOTE_RECEIVED,
            event_key="workflow-test-owner-unread",
            rfq=self.rfq,
        )
        other_notification = Notification.objects.create(
            user=self.other_owner,
            kind=Notification.Kind.QUOTE_RECEIVED,
            event_key="workflow-test-other-owner-unread",
            rfq=self.rfq,
        )
        self.client.force_login(self.owner)

        get_response = self.client.get(reverse("notifications_mark_all_read"))
        post_response = self.client.post(reverse("notifications_mark_all_read"))

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(post_response.status_code, 302)
        owner_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertTrue(owner_notification.is_read)
        self.assertFalse(other_notification.is_read)
