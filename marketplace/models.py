from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class Unit(models.TextChoices):
    PIECE = "piece", _("Piece")
    PROJECT = "project", _("Project")
    SERVICE = "service", _("Service")
    CARTON = "carton", _("Carton")
    KILOGRAM = "kilogram", _("Kilogram")


class Category(models.Model):
    name = models.CharField(_("Category"), max_length=120, unique=True)
    name_en = models.CharField(_("Category name in English"), max_length=120, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        from django.utils.translation import get_language

        if get_language() == "en" and self.name_en:
            return self.name_en
        return self.name


class Product(models.Model):
    supplier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="products",
        limit_choices_to={"role": "supplier"},
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    sku = models.CharField(_("SKU"), max_length=80, blank=True)
    name = models.CharField(_("Product or service"), max_length=180)
    description = models.TextField(_("Description"), blank=True)
    unit = models.CharField(
        _("Unit"),
        max_length=50,
        choices=Unit.choices,
        default=Unit.PIECE,
    )
    minimum_order_quantity = models.DecimalField(
        _("Minimum order quantity"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
        default=1,
    )
    price = models.DecimalField(
        _("Reference price"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Product or service")
        verbose_name_plural = _("Products and services")
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "name"],
                name="unique_product_name_per_supplier",
            ),
            models.CheckConstraint(
                condition=Q(minimum_order_quantity__gt=0),
                name="product_minimum_quantity_positive",
            ),
        ]

    def __str__(self):
        return self.name


class RFQ(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        OPEN = "open", _("Open for quotes")
        EVALUATING = "evaluating", _("Under evaluation")
        AWARDED = "awarded", _("Quote awarded")
        CLOSED = "closed", _("Closed")
        CANCELLED = "cancelled", _("Cancelled")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rfqs",
        limit_choices_to={"role": "owner"},
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="rfqs")
    title = models.CharField(_("Request title"), max_length=200)
    description = models.TextField(_("Requirements details"))
    delivery_city = models.CharField(_("Delivery city"), max_length=100)
    deadline = models.DateField(_("Quote submission deadline"))
    budget_min = models.DecimalField(_("Minimum budget"), max_digits=12, decimal_places=2, null=True, blank=True)
    budget_max = models.DecimalField(_("Maximum budget"), max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Request for quotation")
        verbose_name_plural = _("Requests for quotation")

    def __str__(self):
        return self.title


class RFQItem(models.Model):
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(_("Item"), max_length=180)
    quantity = models.DecimalField(_("Quantity"), max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    unit = models.CharField(
        _("Unit"),
        max_length=50,
        choices=Unit.choices,
        default=Unit.PIECE,
    )
    specifications = models.CharField(_("Specifications"), max_length=255, blank=True)

    def __str__(self):
        return f"{self.name} — {self.quantity} {self.unit}"


class Quote(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending review")
        SELECTED = "selected", _("Selected")
        REJECTED = "rejected", _("Not selected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="quotes")
    supplier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quotes",
        limit_choices_to={"role": "supplier"},
    )
    total_amount = models.DecimalField(
        _("Total quote"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    delivery_days = models.PositiveIntegerField(
        _("Delivery time in days"),
        validators=[MinValueValidator(1)],
    )
    notes = models.TextField(_("Quote notes"), blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rfq", "supplier"], name="one_quote_per_supplier"),
            models.UniqueConstraint(
                fields=["rfq"],
                condition=Q(status="selected"),
                name="one_selected_quote_per_rfq",
            ),
        ]
        ordering = ["total_amount", "delivery_days"]
        verbose_name = _("Quote")
        verbose_name_plural = _("Quotes")

    def __str__(self):
        return f"{self.supplier} — {self.rfq}"


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        AWAITING_CONFIRMATION = "awaiting_confirmation", _("Awaiting supplier confirmation")
        CONFIRMED = "confirmed", _("Confirmed")
        PREPARING = "preparing", _("Preparing")
        SHIPPED = "shipped", _("Shipped")
        DELIVERED = "delivered", _("Delivered")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    rfq = models.OneToOneField(
        RFQ,
        on_delete=models.PROTECT,
        related_name="purchase_order",
        null=True,
        blank=True,
    )
    quote = models.OneToOneField(Quote, on_delete=models.PROTECT, related_name="purchase_order")
    order_number = models.CharField(_("Order number"), max_length=30, unique=True)
    agreed_amount = models.DecimalField(
        _("Agreed amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    currency = models.CharField(_("Currency"), max_length=3, default="USD")
    delivery_days = models.PositiveIntegerField(
        _("Agreed delivery time in days"),
        validators=[MinValueValidator(1)],
        default=1,
    )
    expected_delivery_date = models.DateField(_("Expected delivery date"), null=True, blank=True)
    tracking_reference = models.CharField(_("Tracking reference"), max_length=120, blank=True)
    status = models.CharField(
        _("Order status"),
        max_length=30,
        choices=Status.choices,
        default=Status.AWAITING_CONFIRMATION,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Purchase order")
        verbose_name_plural = _("Purchase orders")
        constraints = [
            models.CheckConstraint(
                condition=Q(agreed_amount__gt=0),
                name="purchase_order_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(delivery_days__gte=1),
                name="purchase_order_delivery_days_positive",
            ),
        ]

    def __str__(self):
        return self.order_number

    @property
    def owner(self):
        return self.rfq.owner if self.rfq_id else self.quote.rfq.owner

    @property
    def supplier(self):
        return self.quote.supplier


class PurchaseOrderItem(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(_("Item"), max_length=180)
    quantity = models.DecimalField(
        _("Quantity"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    unit = models.CharField(_("Unit"), max_length=50, choices=Unit.choices)
    specifications = models.CharField(_("Specifications"), max_length=255, blank=True)

    class Meta:
        ordering = ["pk"]
        verbose_name = _("Purchase order item")
        verbose_name_plural = _("Purchase order items")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="purchase_order_item_quantity_positive",
            ),
        ]

    def __str__(self):
        return f"{self.order} — {self.name}"


class OrderStatusEvent(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="status_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="order_status_events",
    )
    from_status = models.CharField(_("Previous status"), max_length=30, blank=True)
    to_status = models.CharField(_("New status"), max_length=30, choices=PurchaseOrder.Status.choices)
    note = models.TextField(_("Status note"), blank=True)
    actor_role = models.CharField(_("Actor role"), max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = _("Order status event")
        verbose_name_plural = _("Order status events")

    def __str__(self):
        return f"{self.order} — {self.get_to_status_display()}"


class Payment(models.Model):
    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", _("Bank transfer")
        CASH_ON_DELIVERY = "cash_on_delivery", _("Cash on delivery")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUBMITTED = "submitted", _("Payment details submitted")
        CONFIRMED = "confirmed", _("Payment confirmed")
        REJECTED = "rejected", _("Payment rejected")
        REFUNDED = "refunded", _("Refunded")

    order = models.OneToOneField(PurchaseOrder, on_delete=models.PROTECT, related_name="payment")
    method = models.CharField(_("Payment method"), max_length=30, choices=Method.choices)
    amount = models.DecimalField(
        _("Payment amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    status = models.CharField(_("Payment status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    reference = models.CharField(_("Transfer reference"), max_length=120, blank=True)
    note = models.TextField(_("Payment note"), blank=True)
    admin_note = models.TextField(_("Administration note"), blank=True)
    submission_count = models.PositiveIntegerField(_("Submission count"), default=0)
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.order} — {self.get_status_display()}"


class Review(models.Model):
    order = models.OneToOneField(PurchaseOrder, on_delete=models.PROTECT, related_name="review")
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1, rating__lte=5),
                name="review_rating_between_1_and_5",
            ),
        ]

    def __str__(self):
        return f"{self.order} — {self.rating}/5"


class Notification(models.Model):
    class Kind(models.TextChoices):
        QUOTE_RECEIVED = "quote_received", _("Quote received")
        QUOTE_SELECTED = "quote_selected", _("Quote selected")
        QUOTE_REJECTED = "quote_rejected", _("Quote not selected")
        ORDER_STATUS = "order_status", _("Order status updated")
        PAYMENT_SUBMITTED = "payment_submitted", _("Payment submitted")
        PAYMENT_CONFIRMED = "payment_confirmed", _("Payment confirmed")
        PAYMENT_REJECTED = "payment_rejected", _("Payment rejected")
        REVIEW_RECEIVED = "review_received", _("Review received")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_notifications",
    )
    kind = models.CharField(_("Notification type"), max_length=30, choices=Kind.choices)
    event_key = models.CharField(_("Event key"), max_length=120, unique=True)
    payload = models.JSONField(_("Notification context"), default=dict, blank=True)
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    is_read = models.BooleanField(_("Read"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read", "-created_at"])]
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")

    def __str__(self):
        return f"{self.user} — {self.get_kind_display()}"

    @property
    def message(self):
        from django.utils.translation import gettext

        payload = self.payload if isinstance(self.payload, dict) else {}
        request_title = payload.get("request_title") or (self.rfq.title if self.rfq else "")
        order_number = payload.get("order_number") or (
            self.order.order_number if self.order else ""
        )
        if self.kind == self.Kind.QUOTE_RECEIVED:
            return gettext("A new quote was received for “%(request)s”.") % {"request": request_title}
        if self.kind == self.Kind.QUOTE_SELECTED:
            return gettext("Your quote for “%(request)s” was selected.") % {"request": request_title}
        if self.kind == self.Kind.QUOTE_REJECTED:
            return gettext("Another quote was selected for “%(request)s”.") % {"request": request_title}
        if self.kind == self.Kind.ORDER_STATUS:
            status_value = payload.get("status")
            if not status_value and self.event_key.startswith("order-status:"):
                event_key_parts = self.event_key.split(":")
                if len(event_key_parts) >= 4:
                    status_value = event_key_parts[2]
            if status_value:
                try:
                    status = PurchaseOrder.Status(status_value).label
                except ValueError:
                    status = status_value
            else:
                status = self.order.get_status_display() if self.order else ""
            return gettext("Order %(order)s status changed to %(status)s.") % {
                "order": order_number,
                "status": status,
            }
        if self.kind == self.Kind.PAYMENT_SUBMITTED:
            return gettext("Payment details were submitted for order %(order)s.") % {"order": order_number}
        if self.kind == self.Kind.PAYMENT_CONFIRMED:
            return gettext("Payment was confirmed for order %(order)s.") % {"order": order_number}
        if self.kind == self.Kind.PAYMENT_REJECTED:
            return gettext("Payment details require review for order %(order)s.") % {"order": order_number}
        if self.kind == self.Kind.REVIEW_RECEIVED:
            return gettext("A new review was received for order %(order)s.") % {"order": order_number}
        return self.get_kind_display()

    @property
    def target_url_name(self):
        if self.order_id:
            return "order_detail"
        if self.rfq_id:
            return "rfq_detail"
        return "notification_list"
