from django.contrib import admin

from .models import (
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
)


class RFQItemInline(admin.TabularInline):
    model = RFQItem
    extra = 0


@admin.register(RFQ)
class RFQAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "category", "status", "deadline", "created_at")
    list_filter = ("status", "category", "delivery_city")
    search_fields = ("title", "description", "owner__username")
    inlines = [RFQItemInline]


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("rfq", "supplier", "total_amount", "delivery_days", "status")
    list_filter = ("status",)


admin.site.register(Category)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "supplier", "category", "price", "is_active", "updated_at")
    list_filter = ("is_active", "category")
    search_fields = ("name", "sku", "supplier__username", "supplier__company__trade_name")


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0
    readonly_fields = ("name", "quantity", "unit", "specifications")
    can_delete = False


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = (
        "actor",
        "actor_role",
        "from_status",
        "to_status",
        "note",
        "created_at",
    )
    can_delete = False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "rfq",
        "supplier_name",
        "agreed_amount",
        "status",
        "created_at",
    )
    list_filter = ("status", "currency")
    search_fields = (
        "order_number",
        "rfq__title",
        "quote__supplier__username",
        "quote__supplier__company__trade_name",
    )
    inlines = (PurchaseOrderItemInline, OrderStatusEventInline)

    @admin.display(description="Supplier")
    def supplier_name(self, obj):
        return obj.quote.supplier


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "method", "amount", "status", "submitted_at", "confirmed_by")
    list_filter = ("method", "status")
    search_fields = ("order__order_number", "reference")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("order", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("order__order_number", "comment")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    search_fields = ("user__username", "event_key")
    readonly_fields = ("event_key", "payload", "created_at")
