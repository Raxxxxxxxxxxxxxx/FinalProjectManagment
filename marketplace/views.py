import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.models import CompanyProfile, User

from .forms import (
    OrderProgressForm,
    PaymentDecisionForm,
    PaymentForm,
    ProductForm,
    QuoteForm,
    ReviewForm,
    RFQForm,
    RFQItemFormSet,
)
from .models import Category, Notification, Payment, Product, PurchaseOrder, Quote, Review, RFQ
from .services import (
    WorkflowConflict,
    allowed_next_status,
    award_quote,
    create_review,
    decide_payment,
    submit_quote,
    submit_payment,
    transition_order,
    update_quote,
    withdraw_quote,
)


def error_403(request, exception=None):
    return render(request, "403.html", status=403)


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def _supplier_is_verified(user):
    return (
        user.is_authenticated
        and user.is_supplier
        and CompanyProfile.objects.filter(user=user, is_verified=True).exists()
    )


def home(request):
    featured_requests = list(
        RFQ.objects.filter(status=RFQ.Status.OPEN, deadline__gte=timezone.localdate())
        .select_related("category", "owner")
        .annotate(quote_count=Count("quotes"))[:3]
    )
    savings = []
    awarded_orders = (
        PurchaseOrder.objects.exclude(status=PurchaseOrder.Status.CANCELLED)
        .select_related("rfq")
        .prefetch_related("rfq__quotes")
    )
    for order in awarded_orders:
        amounts = [quote.total_amount for quote in order.rfq.quotes.all()]
        if amounts:
            average = sum(amounts) / len(amounts)
            if average > 0 and order.agreed_amount < average:
                savings.append(float((average - order.agreed_amount) * 100 / average))
    return render(
        request,
        "marketplace/home.html",
        {
            "featured_requests": featured_requests,
            "preview_request": featured_requests[0] if featured_requests else None,
            "open_count": RFQ.objects.filter(
                status=RFQ.Status.OPEN,
                deadline__gte=timezone.localdate(),
            ).count(),
            "supplier_count": User.objects.filter(
                role=User.Role.SUPPLIER,
                company__is_verified=True,
            ).count(),
            "category_count": Category.objects.count(),
            "total_quote_count": Quote.objects.count(),
            "estimated_savings": round(sum(savings) / len(savings)) if savings else 0,
        },
    )


@login_required
def dashboard(request):
    if request.user.is_staff:
        requests = (
            RFQ.objects.select_related("category")
            .annotate(quote_count=Count("quotes"))[:5]
        )
        stats = {
            "users": User.objects.count(),
            "requests": RFQ.objects.count(),
            "quotes": Quote.objects.count(),
            "orders": PurchaseOrder.objects.count(),
        }
        attention = {
            "primary": CompanyProfile.objects.filter(
                user__role=User.Role.SUPPLIER,
                is_verified=False,
            ).count(),
            "secondary": Payment.objects.filter(
                status=Payment.Status.SUBMITTED
            ).count(),
        }
    elif request.user.is_supplier:
        requests = (
            RFQ.objects.filter(
                status=RFQ.Status.OPEN,
                deadline__gte=timezone.localdate(),
            )
            .select_related("category")
            .annotate(quote_count=Count("quotes"))[:5]
        )
        stats = {
            "available": RFQ.objects.filter(
                status=RFQ.Status.OPEN,
                deadline__gte=timezone.localdate(),
            ).count(),
            "quotes": request.user.quotes.count(),
            "selected": request.user.quotes.filter(status=Quote.Status.SELECTED).count(),
            "orders": PurchaseOrder.objects.filter(quote__supplier=request.user).count(),
        }
        attention = {
            "primary": PurchaseOrder.objects.filter(
                quote__supplier=request.user,
                status=PurchaseOrder.Status.AWAITING_CONFIRMATION,
            ).count(),
            "secondary": 0 if _supplier_is_verified(request.user) else 1,
        }
    else:
        requests = request.user.rfqs.select_related("category").annotate(quote_count=Count("quotes"))[:5]
        stats = {
            "requests": request.user.rfqs.count(),
            "open": request.user.rfqs.filter(status=RFQ.Status.OPEN).count(),
            "quotes": Quote.objects.filter(rfq__owner=request.user).count(),
            "orders": PurchaseOrder.objects.filter(rfq__owner=request.user).count(),
        }
        attention = {
            "primary": PurchaseOrder.objects.filter(
                rfq__owner=request.user,
                status=PurchaseOrder.Status.DELIVERED,
            ).count(),
            "secondary": request.user.rfqs.filter(
                status=RFQ.Status.OPEN,
                quotes__status=Quote.Status.PENDING,
            )
            .distinct()
            .count(),
        }
    attention["total"] = attention["primary"] + attention["secondary"]
    return render(
        request,
        "marketplace/dashboard.html",
        {"requests": requests, "stats": stats, "attention": attention},
    )


def rfq_list(request):
    rfqs = (
        RFQ.objects.filter(
            status=RFQ.Status.OPEN,
            deadline__gte=timezone.localdate(),
        )
        .select_related("category", "owner", "owner__company")
        .annotate(quote_count=Count("quotes"))
    )
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if query:
        rfqs = rfqs.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(delivery_city__icontains=query)
        )
    if category:
        rfqs = rfqs.filter(category__slug=category)
    rfqs = rfqs.order_by("-created_at", "-pk")
    page_obj = Paginator(rfqs, 9).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/rfq_list.html",
        {
            "rfqs": page_obj,
            "page_obj": page_obj,
            "categories": Category.objects.all(),
            "query": query,
            "selected_category": category,
        },
    )


def product_list(request):
    products = (
        Product.objects.filter(
            is_active=True,
            supplier__company__is_verified=True,
        )
        .select_related("category", "supplier", "supplier__company")
    )
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(description__icontains=query)
            | Q(sku__icontains=query)
            | Q(supplier__company__trade_name__icontains=query)
        )
    if category:
        products = products.filter(category__slug=category)
    page_obj = Paginator(products, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/product_list.html",
        {
            "products": page_obj,
            "page_obj": page_obj,
            "categories": Category.objects.all(),
            "query": query,
            "selected_category": category,
        },
    )


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related("category", "supplier", "supplier__company"),
        pk=pk,
        is_active=True,
        supplier__company__is_verified=True,
    )
    return render(request, "marketplace/product_detail.html", {"product": product})


def supplier_profile(request, pk):
    supplier = get_object_or_404(
        User.objects.select_related("company"),
        pk=pk,
        role=User.Role.SUPPLIER,
    )
    if not supplier.company.is_verified and not (
        request.user.is_authenticated
        and (request.user == supplier or request.user.is_staff)
    ):
        raise Http404
    products = supplier.products.filter(is_active=True).select_related("category")[:8]
    rating_summary = Review.objects.filter(order__quote__supplier=supplier).aggregate(
        value=Avg("rating"),
        count=Count("pk"),
    )
    completed_orders = PurchaseOrder.objects.filter(
        quote__supplier=supplier,
        status=PurchaseOrder.Status.COMPLETED,
    ).count()
    return render(
        request,
        "marketplace/supplier_profile.html",
        {
            "supplier_profile": supplier,
            "products": products,
            "rating": rating_summary["value"],
            "review_count": rating_summary["count"],
            "completed_orders": completed_orders,
        },
    )


@login_required
def my_products(request):
    if not request.user.is_supplier:
        raise Http404
    products = request.user.products.select_related("category")
    page_obj = Paginator(products, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/my_products.html",
        {"products": page_obj, "page_obj": page_obj},
    )


@login_required
def product_create(request):
    if not request.user.is_supplier:
        raise Http404
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        product = form.save(commit=False)
        product.supplier = request.user
        product.save()
        messages.success(request, _("The product or service was added successfully."))
        return redirect("my_products")
    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "editing": False},
    )


@login_required
def product_edit(request, pk):
    if not request.user.is_supplier:
        raise Http404
    product = get_object_or_404(Product, pk=pk, supplier=request.user)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("The product or service was updated successfully."))
        return redirect("my_products")
    return render(
        request,
        "marketplace/product_form.html",
        {"form": form, "product": product, "editing": True},
    )


@login_required
@require_POST
def product_toggle(request, pk):
    if not request.user.is_supplier:
        raise Http404
    product = get_object_or_404(Product, pk=pk, supplier=request.user)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active", "updated_at"])
    messages.success(
        request,
        _("The catalog item visibility was updated."),
    )
    return redirect("my_products")


@login_required
def my_quotes(request):
    if not request.user.is_supplier:
        raise Http404
    quotes = request.user.quotes.select_related("rfq", "rfq__category").order_by(
        "-created_at",
        "-pk",
    )
    status = request.GET.get("status", "")
    if status in Quote.Status.values:
        quotes = quotes.filter(status=status)
    page_obj = Paginator(quotes, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/my_quotes.html",
        {
            "quotes": page_obj,
            "page_obj": page_obj,
            "statuses": Quote.Status.choices,
            "selected_status": status,
        },
    )


@login_required
def quote_edit(request, pk):
    if not request.user.is_supplier:
        raise Http404
    if not _supplier_is_verified(request.user):
        messages.error(
            request,
            _("Your supplier account must be verified before managing quotes."),
        )
        return redirect("profile")
    quote = get_object_or_404(
        Quote.objects.select_related("rfq"),
        pk=pk,
        supplier=request.user,
        status=Quote.Status.PENDING,
        rfq__status=RFQ.Status.OPEN,
        rfq__deadline__gte=timezone.localdate(),
    )
    form = QuoteForm(request.POST or None, instance=quote)
    if request.method == "POST" and form.is_valid():
        try:
            update_quote(
                quote_id=quote.pk,
                supplier=request.user,
                total_amount=form.cleaned_data["total_amount"],
                delivery_days=form.cleaned_data["delivery_days"],
                notes=form.cleaned_data.get("notes", ""),
            )
        except (WorkflowConflict, PermissionDenied) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Your quote was updated successfully."))
            return redirect("my_quotes")
    return render(
        request,
        "marketplace/quote_edit.html",
        {"form": form, "quote": quote, "rfq": quote.rfq},
    )


@login_required
@require_POST
def quote_withdraw(request, pk):
    if not request.user.is_supplier:
        raise Http404
    quote = get_object_or_404(Quote, pk=pk, supplier=request.user)
    try:
        withdraw_quote(quote_id=quote.pk, supplier=request.user)
    except (WorkflowConflict, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Your quote was withdrawn."))
    return redirect("my_quotes")


@login_required
@transaction.atomic
def rfq_create(request):
    if not request.user.is_owner:
        raise PermissionDenied(_("Creating requests is available to project owners only."))

    form = RFQForm(request.POST or None)
    formset = RFQItemFormSet(request.POST or None, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        rfq = form.save(commit=False)
        rfq.owner = request.user
        rfq.status = RFQ.Status.OPEN
        rfq.save()
        formset.instance = rfq
        formset.save()
        messages.success(request, _("The request for quotation has been published successfully."))
        return redirect("rfq_detail", pk=rfq.pk)
    return render(request, "marketplace/rfq_form.html", {"form": form, "formset": formset})


def rfq_detail(request, pk):
    public_statuses = {RFQ.Status.OPEN, RFQ.Status.EVALUATING}
    rfq = get_object_or_404(
        RFQ.objects.select_related("owner", "owner__company", "category").prefetch_related("items", "quotes__supplier"),
        pk=pk,
    )
    if (
        rfq.status not in public_statuses
        and not (
            request.user.is_authenticated
            and (request.user == rfq.owner or request.user.is_staff)
        )
    ):
        raise Http404
    can_view_quotes = request.user.is_authenticated and (
        request.user == rfq.owner or request.user.is_staff
    )
    user_quote = None
    supplier_can_quote = False
    if request.user.is_authenticated and request.user.is_supplier:
        user_quote = rfq.quotes.filter(supplier=request.user).first()
        supplier_can_quote = _supplier_is_verified(request.user)
    return render(
        request,
        "marketplace/rfq_detail.html",
        {
            "rfq": rfq,
            "can_view_quotes": can_view_quotes,
            "user_quote": user_quote,
            "supplier_can_quote": supplier_can_quote,
        },
    )


@login_required
def quote_create(request, pk):
    rfq = get_object_or_404(
        RFQ,
        pk=pk,
        status=RFQ.Status.OPEN,
        deadline__gte=timezone.localdate(),
    )
    if not request.user.is_supplier:
        raise PermissionDenied(_("Submitting quotes is available to suppliers only."))
    if not _supplier_is_verified(request.user):
        messages.error(
            request,
            _("Complete your organization profile and wait for verification before submitting quotes."),
        )
        return redirect("profile")
    if rfq.quotes.filter(supplier=request.user).exists():
        messages.info(request, _("You have already submitted a quote for this request."))
        return redirect("rfq_detail", pk=rfq.pk)

    form = QuoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            submit_quote(
                rfq_id=rfq.pk,
                supplier=request.user,
                total_amount=form.cleaned_data["total_amount"],
                delivery_days=form.cleaned_data["delivery_days"],
                notes=form.cleaned_data.get("notes", ""),
            )
        except WorkflowConflict as exc:
            messages.info(request, str(exc))
            return redirect("rfq_detail", pk=rfq.pk)
        messages.success(request, _("Your quote has been sent to the project owner."))
        return redirect("rfq_detail", pk=rfq.pk)
    return render(request, "marketplace/quote_form.html", {"form": form, "rfq": rfq})


@login_required
def rfq_compare(request, pk):
    rfq = get_object_or_404(
        RFQ.objects.select_related("category").prefetch_related(
            "quotes__supplier__company"
        ),
        pk=pk,
    )
    if request.user != rfq.owner and not request.user.is_staff:
        raise Http404
    quotes = list(rfq.quotes.exclude(status=Quote.Status.WITHDRAWN))
    for quote in quotes:
        supplier_company = getattr(quote.supplier, "company", None)
        quote.is_eligible = (
            quote.supplier.is_active
            and bool(supplier_company)
            and supplier_company.is_verified
        )
        rating_summary = Review.objects.filter(
            order__quote__supplier=quote.supplier
        ).aggregate(value=Avg("rating"), count=Count("pk"))
        quote.supplier_rating = rating_summary["value"]
        quote.supplier_review_count = rating_summary["count"]
    lowest_quote = min(quotes, key=lambda quote: quote.total_amount) if quotes else None
    fastest_quote = min(quotes, key=lambda quote: quote.delivery_days) if quotes else None
    eligible_quotes = [quote for quote in quotes if quote.is_eligible]
    scoring_basis = eligible_quotes or quotes
    scoring_lowest = (
        min(scoring_basis, key=lambda quote: quote.total_amount)
        if scoring_basis
        else None
    )
    scoring_fastest = (
        min(scoring_basis, key=lambda quote: quote.delivery_days)
        if scoring_basis
        else None
    )
    for quote in quotes:
        rating_value = float(quote.supplier_rating or 3)
        quote.decision_score = round(
            min(float(scoring_lowest.total_amount / quote.total_amount), 1) * 50
            + min(float(scoring_fastest.delivery_days / quote.delivery_days), 1) * 30
            + rating_value / 5 * 20,
            1,
        )
    recommended_quote = (
        max(eligible_quotes, key=lambda quote: quote.decision_score)
        if eligible_quotes
        else None
    )
    average_amount = (
        rfq.quotes.exclude(status=Quote.Status.WITHDRAWN).aggregate(
            value=Avg("total_amount")
        )["value"]
        if quotes
        else None
    )
    return render(
        request,
        "marketplace/rfq_compare.html",
        {
            "rfq": rfq,
            "quotes": quotes,
            "lowest_quote": lowest_quote,
            "fastest_quote": fastest_quote,
            "average_amount": average_amount,
            "recommended_quote": recommended_quote,
        },
    )


@login_required
@require_POST
def quote_award(request, quote_pk):
    quote = get_object_or_404(Quote, pk=quote_pk, rfq__owner=request.user)
    try:
        order, created = award_quote(quote_id=quote.pk, actor=request.user)
    except WorkflowConflict as exc:
        messages.error(request, str(exc))
        return redirect("rfq_compare", pk=quote.rfq_id)
    if created:
        messages.success(request, _("The quote was selected and a purchase order was created."))
    else:
        messages.info(request, _("This quote has already been selected."))
    return redirect("order_detail", pk=order.pk)


def _orders_for_user(user):
    queryset = PurchaseOrder.objects.select_related(
        "rfq__owner__company",
        "quote__supplier__company",
        "quote__rfq__category",
        "payment",
    ).prefetch_related("status_events__actor", "items")
    if user.is_staff:
        return queryset
    if user.is_owner:
        return queryset.filter(rfq__owner=user)
    if user.is_supplier:
        return queryset.filter(quote__supplier=user)
    return queryset.none()


@login_required
def order_list(request):
    orders = _orders_for_user(request.user)
    status = request.GET.get("status", "")
    if status in PurchaseOrder.Status.values:
        orders = orders.filter(status=status)
    page_obj = Paginator(orders, 12).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/order_list.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "statuses": PurchaseOrder.Status.choices,
            "selected_status": status,
        },
    )


@login_required
def order_detail(request, pk):
    order = get_object_or_404(_orders_for_user(request.user), pk=pk)
    payment = Payment.objects.filter(order=order).first()
    review = Review.objects.filter(order=order).first()
    next_status = allowed_next_status(order, request.user)
    timeline_values = [
        PurchaseOrder.Status.AWAITING_CONFIRMATION,
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.PREPARING,
        PurchaseOrder.Status.SHIPPED,
        PurchaseOrder.Status.DELIVERED,
        PurchaseOrder.Status.COMPLETED,
    ]
    status_labels = dict(PurchaseOrder.Status.choices)
    current_index = (
        timeline_values.index(order.status) if order.status in timeline_values else -1
    )
    status_form = OrderProgressForm(
        show_tracking=next_status == PurchaseOrder.Status.SHIPPED
    )
    return render(
        request,
        "marketplace/order_detail.html",
        {
            "order": order,
            "payment": payment,
            "review": review,
            "next_status": next_status,
            "next_status_label": status_labels.get(next_status),
            "status_form": status_form,
            "timeline_steps": [
                {
                    "value": value,
                    "label": status_labels[value],
                    "reached": index <= current_index,
                    "current": value == order.status,
                }
                for index, value in enumerate(timeline_values)
            ],
        },
    )


@login_required
@require_POST
def order_update_status(request, pk):
    order = get_object_or_404(_orders_for_user(request.user), pk=pk)
    target_status = request.POST.get("target_status", "")
    expected_target = allowed_next_status(order, request.user)
    form = OrderProgressForm(
        request.POST,
        show_tracking=expected_target == PurchaseOrder.Status.SHIPPED,
    )
    if target_status != expected_target:
        messages.error(request, _("This order action is no longer available."))
        return redirect("order_detail", pk=order.pk)
    if not form.is_valid():
        messages.error(request, _("Please review the progress information."))
        return redirect("order_detail", pk=order.pk)
    try:
        transition_order(
            order_id=order.pk,
            actor=request.user,
            target_status=target_status,
            note=form.cleaned_data.get("note", ""),
            tracking_reference=form.cleaned_data.get("tracking_reference", ""),
        )
    except (WorkflowConflict, PermissionDenied) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("The order status was updated successfully."))
    return redirect("order_detail", pk=order.pk)


@login_required
def payment_submit(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("rfq__owner", "quote__supplier"),
        pk=pk,
        rfq__owner=request.user,
    )
    existing = Payment.objects.filter(order=order).first()
    initial = {}
    if existing:
        initial = {
            "method": existing.method,
            "reference": existing.reference,
            "note": existing.note,
        }
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            submit_payment(
                order_id=order.pk,
                actor=request.user,
                method=form.cleaned_data["method"],
                reference=form.cleaned_data.get("reference", ""),
                note=form.cleaned_data.get("note", ""),
            )
        except WorkflowConflict as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Payment details were submitted for review."))
            return redirect("order_detail", pk=order.pk)
    return render(
        request,
        "marketplace/payment_form.html",
        {"order": order, "form": form, "payment": existing},
    )


@login_required
def payment_review(request, payment_pk):
    if not request.user.is_staff:
        raise Http404
    payment = get_object_or_404(
        Payment.objects.select_related("order__rfq__owner", "order__quote__supplier"),
        pk=payment_pk,
    )
    form = PaymentDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            decide_payment(
                payment_id=payment.pk,
                actor=request.user,
                decision=form.cleaned_data["decision"],
                admin_note=form.cleaned_data.get("admin_note", ""),
            )
        except WorkflowConflict as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("The payment decision was recorded."))
            return redirect("order_detail", pk=payment.order_id)
    return render(
        request,
        "marketplace/payment_review.html",
        {"payment": payment, "order": payment.order, "form": form},
    )


@login_required
def review_create(request, pk):
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("rfq__owner", "quote__supplier"),
        pk=pk,
        rfq__owner=request.user,
    )
    if Review.objects.filter(order=order).exists():
        messages.info(request, _("This order has already been reviewed."))
        return redirect("order_detail", pk=order.pk)
    form = ReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_review(
                order_id=order.pk,
                actor=request.user,
                rating=form.cleaned_data["rating"],
                comment=form.cleaned_data.get("comment", ""),
            )
        except WorkflowConflict as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Thank you. Your review was submitted."))
            return redirect("order_detail", pk=order.pk)
    return render(
        request,
        "marketplace/review_form.html",
        {"order": order, "form": form},
    )


@login_required
def notification_list(request):
    notifications = request.user.notifications.select_related("rfq", "order")
    page_obj = Paginator(notifications, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "marketplace/notification_list.html",
        {"notifications": page_obj, "page_obj": page_obj},
    )


@login_required
@require_POST
def notification_open(request, pk):
    notification = get_object_or_404(
        request.user.notifications.select_related("rfq", "order"),
        pk=pk,
    )
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    if notification.order_id:
        return redirect("order_detail", pk=notification.order_id)
    if notification.rfq_id:
        return redirect("rfq_detail", pk=notification.rfq_id)
    return redirect("notification_list")


@login_required
@require_POST
def notifications_mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    messages.success(request, _("All notifications were marked as read."))
    return redirect("notification_list")


@login_required
def reports(request):
    if request.user.is_staff:
        orders = PurchaseOrder.objects.all()
        financial_orders = orders.exclude(status=PurchaseOrder.Status.CANCELLED)
        metrics = {
            "users": User.objects.count(),
            "requests": RFQ.objects.count(),
            "orders": orders.count(),
            "volume": financial_orders.aggregate(value=Sum("agreed_amount"))["value"] or 0,
            "pending_payments": Payment.objects.filter(status=Payment.Status.SUBMITTED).count(),
        }
        report_role = "staff"
    elif request.user.is_supplier:
        orders = PurchaseOrder.objects.filter(quote__supplier=request.user)
        financial_orders = orders.exclude(status=PurchaseOrder.Status.CANCELLED)
        quote_count = request.user.quotes.count()
        selected_count = request.user.quotes.filter(status=Quote.Status.SELECTED).count()
        metrics = {
            "quotes": quote_count,
            "orders": orders.count(),
            "revenue": financial_orders.aggregate(value=Sum("agreed_amount"))["value"] or 0,
            "win_rate": round(selected_count * 100 / quote_count, 1) if quote_count else 0,
            "rating": Review.objects.filter(order__quote__supplier=request.user).aggregate(
                value=Avg("rating")
            )["value"],
        }
        report_role = "supplier"
    else:
        orders = PurchaseOrder.objects.filter(rfq__owner=request.user)
        financial_orders = orders.exclude(status=PurchaseOrder.Status.CANCELLED)
        metrics = {
            "requests": request.user.rfqs.count(),
            "orders": orders.count(),
            "spend": financial_orders.aggregate(value=Sum("agreed_amount"))["value"] or 0,
            "completed": orders.filter(status=PurchaseOrder.Status.COMPLETED).count(),
            "open": request.user.rfqs.filter(status=RFQ.Status.OPEN).count(),
        }
        report_role = "owner"
    status_distribution = [
        {
            "value": status,
            "label": label,
            "count": orders.filter(status=status).count(),
        }
        for status, label in PurchaseOrder.Status.choices
    ]
    return render(
        request,
        "marketplace/reports.html",
        {
            "metrics": metrics,
            "report_role": report_role,
            "status_distribution": status_distribution,
        },
    )


def _csv_safe(value):
    text = str(value or "")
    stripped = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or stripped.startswith(
        ("=", "+", "-", "@")
    ):
        return f"'{text}"
    return text


@login_required
def reports_export_csv(request):
    orders = _orders_for_user(request.user).order_by("-created_at")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="procurement-report.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            _("Order number"),
            _("Request title"),
            _("Project owner"),
            _("Supplier"),
            _("Agreed amount"),
            _("Currency"),
            _("Order status"),
            _("Created at"),
        ]
    )
    for order in orders:
        owner_company = getattr(order.owner, "company", None)
        supplier_company = getattr(order.supplier, "company", None)
        writer.writerow(
            [
                _csv_safe(order.order_number),
                _csv_safe(order.rfq.title),
                _csv_safe(
                    owner_company.trade_name
                    if owner_company
                    else order.owner.username
                ),
                _csv_safe(
                    supplier_company.trade_name
                    if supplier_company
                    else order.supplier.username
                ),
                order.agreed_amount,
                order.currency,
                order.get_status_display(),
                timezone.localtime(order.created_at).isoformat(),
            ]
        )
    return response
