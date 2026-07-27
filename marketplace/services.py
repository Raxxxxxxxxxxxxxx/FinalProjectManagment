from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import CompanyProfile, User

from .models import (
    Notification,
    OrderStatusEvent,
    Payment,
    PurchaseOrder,
    PurchaseOrderItem,
    Quote,
    Review,
    RFQ,
)


class WorkflowConflict(Exception):
    """Raised when an action conflicts with the current workflow state."""


def _lock_verified_supplier(supplier_id, error_message):
    supplier = User.objects.select_for_update().get(pk=supplier_id)
    company = (
        CompanyProfile.objects.select_for_update()
        .filter(user_id=supplier_id)
        .first()
    )
    if (
        not supplier.is_active
        or not supplier.is_supplier
        or not company
        or not company.is_verified
    ):
        raise WorkflowConflict(error_message)
    return supplier


def _notify(*, user, kind, event_key, actor=None, rfq=None, order=None, payload=None):
    context = {
        "request_title": rfq.title if rfq else "",
        "order_number": order.order_number if order else "",
    }
    if payload:
        context.update(payload)
    notification, _ = Notification.objects.get_or_create(
        event_key=event_key,
        defaults={
            "user": user,
            "actor": actor,
            "kind": kind,
            "rfq": rfq,
            "order": order,
            "payload": context,
        },
    )
    return notification


def notify_quote_received(quote):
    return _notify(
        user=quote.rfq.owner,
        actor=quote.supplier,
        kind=Notification.Kind.QUOTE_RECEIVED,
        event_key=f"quote-received:{quote.pk}:{quote.rfq.owner_id}",
        rfq=quote.rfq,
    )


@transaction.atomic
def submit_quote(*, rfq_id, supplier, total_amount, delivery_days, notes=""):
    rfq = (
        RFQ.objects.select_for_update()
        .select_related("owner")
        .get(pk=rfq_id)
    )
    if rfq.status != RFQ.Status.OPEN or rfq.deadline < timezone.localdate():
        raise WorkflowConflict(_("This request is no longer accepting quotes."))
    locked_supplier = _lock_verified_supplier(
        supplier.pk,
        _("Your supplier account must be verified before submitting quotes."),
    )

    try:
        with transaction.atomic():
            quote = Quote.objects.create(
                rfq=rfq,
                supplier=locked_supplier,
                total_amount=total_amount,
                delivery_days=delivery_days,
                notes=notes.strip(),
            )
    except IntegrityError as exc:
        raise WorkflowConflict(
            _("You have already submitted a quote for this request.")
        ) from exc
    notify_quote_received(quote)
    return quote


@transaction.atomic
def update_quote(*, quote_id, supplier, total_amount, delivery_days, notes=""):
    quote_reference = Quote.objects.only("rfq_id", "supplier_id").get(pk=quote_id)
    rfq = RFQ.objects.select_for_update().get(pk=quote_reference.rfq_id)
    quote = Quote.objects.select_for_update().get(pk=quote_id)
    if quote.supplier_id != supplier.pk:
        raise PermissionDenied(_("You can edit only your own quotes."))
    _lock_verified_supplier(
        supplier.pk,
        _("Your supplier account must be verified before managing quotes."),
    )
    if (
        quote.status != Quote.Status.PENDING
        or rfq.status != RFQ.Status.OPEN
        or rfq.deadline < timezone.localdate()
    ):
        raise WorkflowConflict(_("This quote can no longer be edited."))

    quote.total_amount = total_amount
    quote.delivery_days = delivery_days
    quote.notes = notes.strip()
    quote.save(
        update_fields=["total_amount", "delivery_days", "notes", "updated_at"]
    )
    return quote


@transaction.atomic
def withdraw_quote(*, quote_id, supplier):
    quote_reference = Quote.objects.only("rfq_id", "supplier_id").get(pk=quote_id)
    rfq = RFQ.objects.select_for_update().get(pk=quote_reference.rfq_id)
    quote = Quote.objects.select_for_update().get(pk=quote_id)
    if quote.supplier_id != supplier.pk:
        raise PermissionDenied(_("You can withdraw only your own quotes."))
    if (
        quote.status != Quote.Status.PENDING
        or rfq.status != RFQ.Status.OPEN
        or rfq.deadline < timezone.localdate()
    ):
        raise WorkflowConflict(_("This quote can no longer be withdrawn."))

    quote.status = Quote.Status.WITHDRAWN
    quote.save(update_fields=["status", "updated_at"])
    return quote


def _order_number(rfq):
    return f"SU-{timezone.localdate():%Y%m%d}-{rfq.pk:05d}-{uuid4().hex[:5].upper()}"


@transaction.atomic
def award_quote(*, quote_id, actor):
    quote_reference = Quote.objects.only("rfq_id", "supplier_id").get(pk=quote_id)
    rfq = RFQ.objects.select_for_update().get(pk=quote_reference.rfq_id)
    quote = (
        Quote.objects.select_for_update()
        .select_related("rfq__owner", "supplier")
        .get(pk=quote_id)
    )

    if actor != rfq.owner:
        raise PermissionDenied(_("Only the request owner can select the winning quote."))

    existing_order = (
        PurchaseOrder.objects.select_for_update()
        .filter(rfq=rfq)
        .select_related("quote")
        .first()
    )
    if existing_order:
        if existing_order.quote_id == quote.pk:
            return existing_order, False
        raise WorkflowConflict(_("A different quote has already been selected for this request."))

    _lock_verified_supplier(
        quote.supplier_id,
        _("The selected supplier is no longer active and verified."),
    )
    if rfq.status not in {RFQ.Status.OPEN, RFQ.Status.EVALUATING}:
        raise WorkflowConflict(_("This request is no longer accepting a selection."))
    if quote.status != Quote.Status.PENDING:
        raise WorkflowConflict(_("This quote is no longer available for selection."))

    competing_quotes = list(
        Quote.objects.select_for_update()
        .filter(rfq=rfq)
        .exclude(pk=quote.pk)
        .select_related("supplier")
    )

    quote.status = Quote.Status.SELECTED
    quote.save(update_fields=["status", "updated_at"])
    Quote.objects.filter(rfq=rfq).exclude(pk=quote.pk).filter(
        status=Quote.Status.PENDING
    ).update(status=Quote.Status.REJECTED, updated_at=timezone.now())

    rfq.status = RFQ.Status.AWARDED
    rfq.save(update_fields=["status", "updated_at"])

    try:
        order = PurchaseOrder.objects.create(
            rfq=rfq,
            quote=quote,
            order_number=_order_number(rfq),
            agreed_amount=quote.total_amount,
            delivery_days=quote.delivery_days,
            expected_delivery_date=timezone.localdate() + timedelta(days=quote.delivery_days),
            status=PurchaseOrder.Status.AWAITING_CONFIRMATION,
        )
    except IntegrityError as exc:
        raise WorkflowConflict(_("A purchase order already exists for this request.")) from exc

    PurchaseOrderItem.objects.bulk_create(
        [
            PurchaseOrderItem(
                order=order,
                name=item.name,
                quantity=item.quantity,
                unit=item.unit,
                specifications=item.specifications,
            )
            for item in rfq.items.all()
        ]
    )

    OrderStatusEvent.objects.create(
        order=order,
        actor=actor,
        actor_role=actor.role,
        from_status="",
        to_status=PurchaseOrder.Status.AWAITING_CONFIRMATION,
        note=_("The project owner selected the quote."),
    )
    _notify(
        user=quote.supplier,
        actor=actor,
        kind=Notification.Kind.QUOTE_SELECTED,
        event_key=f"quote-selected:{quote.pk}:{quote.supplier_id}",
        rfq=rfq,
        order=order,
    )
    for competing_quote in competing_quotes:
        if competing_quote.status == Quote.Status.PENDING:
            _notify(
                user=competing_quote.supplier,
                actor=actor,
                kind=Notification.Kind.QUOTE_REJECTED,
                event_key=f"quote-rejected:{competing_quote.pk}:{competing_quote.supplier_id}",
                payload={"request_title": rfq.title},
            )
    return order, True


def allowed_next_status(order, actor):
    transitions = {
        PurchaseOrder.Status.AWAITING_CONFIRMATION: PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.CONFIRMED: PurchaseOrder.Status.PREPARING,
        PurchaseOrder.Status.PREPARING: PurchaseOrder.Status.SHIPPED,
        PurchaseOrder.Status.SHIPPED: PurchaseOrder.Status.DELIVERED,
        PurchaseOrder.Status.DELIVERED: PurchaseOrder.Status.COMPLETED,
    }
    target = transitions.get(order.status)
    if not target:
        return None

    if actor.is_staff:
        return target
    if order.status == PurchaseOrder.Status.DELIVERED:
        return target if actor == order.owner else None
    return target if actor == order.supplier else None


def _payment_allows_fulfillment(order):
    try:
        payment = order.payment
    except Payment.DoesNotExist:
        return False
    return payment.status == Payment.Status.CONFIRMED or (
        payment.method == Payment.Method.CASH_ON_DELIVERY
        and payment.status == Payment.Status.SUBMITTED
    )


@transaction.atomic
def transition_order(*, order_id, actor, target_status, note="", tracking_reference=""):
    order = (
        PurchaseOrder.objects.select_for_update()
        .select_related("rfq__owner", "quote__supplier")
        .get(pk=order_id)
    )

    if target_status == order.status:
        return order, False

    allowed_status = allowed_next_status(order, actor)
    if allowed_status != target_status:
        raise PermissionDenied(_("You cannot perform this status transition."))

    if (
        order.status == PurchaseOrder.Status.CONFIRMED
        and target_status == PurchaseOrder.Status.PREPARING
        and not _payment_allows_fulfillment(order)
    ):
        raise WorkflowConflict(_("Payment must be confirmed before preparation begins."))

    previous_status = order.status
    order.status = target_status
    fields = ["status", "updated_at"]
    confirmed_cod_payment = None

    if target_status == PurchaseOrder.Status.SHIPPED and tracking_reference:
        order.tracking_reference = tracking_reference.strip()
        fields.append("tracking_reference")
    if target_status == PurchaseOrder.Status.COMPLETED:
        order.completed_at = timezone.now()
        fields.append("completed_at")
        order.rfq.status = RFQ.Status.CLOSED
        order.rfq.save(update_fields=["status", "updated_at"])
        try:
            payment = order.payment
        except Payment.DoesNotExist:
            payment = None
        if (
            payment
            and payment.method == Payment.Method.CASH_ON_DELIVERY
            and payment.status == Payment.Status.SUBMITTED
        ):
            payment.status = Payment.Status.CONFIRMED
            payment.confirmed_at = timezone.now()
            payment.confirmed_by = actor
            payment.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
            confirmed_cod_payment = payment

    order.save(update_fields=fields)
    OrderStatusEvent.objects.create(
        order=order,
        actor=actor,
        actor_role=actor.role,
        from_status=previous_status,
        to_status=target_status,
        note=note.strip(),
    )

    recipients = []
    if actor != order.owner:
        recipients.append(order.owner)
    if actor != order.supplier:
        recipients.append(order.supplier)
    for recipient in recipients:
        _notify(
            user=recipient,
            actor=actor,
            kind=Notification.Kind.ORDER_STATUS,
            event_key=f"order-status:{order.pk}:{target_status}:{recipient.pk}",
            rfq=order.rfq,
            order=order,
            payload={"status": target_status},
        )
    if confirmed_cod_payment:
        for recipient in (order.owner, order.supplier):
            _notify(
                user=recipient,
                actor=actor,
                kind=Notification.Kind.PAYMENT_CONFIRMED,
                event_key=f"payment-confirmed:{confirmed_cod_payment.pk}:{recipient.pk}",
                rfq=order.rfq,
                order=order,
            )
    return order, True


@transaction.atomic
def submit_payment(*, order_id, actor, method, reference="", note=""):
    order = (
        PurchaseOrder.objects.select_for_update()
        .select_related("rfq__owner", "quote__supplier")
        .get(pk=order_id)
    )
    if actor != order.owner:
        raise PermissionDenied(_("Only the order owner can submit payment details."))
    if order.status not in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.PREPARING,
    }:
        raise WorkflowConflict(_("Payment details cannot be submitted at this order stage."))
    if method == Payment.Method.BANK_TRANSFER and not reference.strip():
        raise WorkflowConflict(_("A transfer reference is required for bank transfers."))

    payment, _ = Payment.objects.select_for_update().get_or_create(
        order=order,
        defaults={
            "method": method,
            "amount": order.agreed_amount,
        },
    )
    if payment.status == Payment.Status.CONFIRMED:
        raise WorkflowConflict(_("This payment has already been confirmed."))

    payment.method = method
    payment.amount = order.agreed_amount
    payment.reference = reference.strip()
    payment.note = note.strip()
    payment.admin_note = ""
    payment.status = Payment.Status.SUBMITTED
    payment.submitted_at = timezone.now()
    payment.submission_count += 1
    payment.save()

    recipients = list(User.objects.filter(is_staff=True, is_active=True))
    if method == Payment.Method.CASH_ON_DELIVERY:
        recipients.append(order.supplier)
    for recipient in {recipient.pk: recipient for recipient in recipients}.values():
        _notify(
            user=recipient,
            actor=actor,
            kind=Notification.Kind.PAYMENT_SUBMITTED,
            event_key=f"payment-submitted:{payment.pk}:{payment.submission_count}:{recipient.pk}",
            rfq=order.rfq,
            order=order,
        )
    return payment


@transaction.atomic
def decide_payment(*, payment_id, actor, decision, admin_note=""):
    if not actor.is_staff:
        raise PermissionDenied(_("Only platform administration can review payments."))

    order = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("rfq__owner", "quote__supplier")
        .get(payment__pk=payment_id)
    )
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    target = {
        "confirm": Payment.Status.CONFIRMED,
        "reject": Payment.Status.REJECTED,
    }.get(decision)
    if not target:
        raise WorkflowConflict(_("Unknown payment decision."))
    if payment.status == target:
        return payment, False
    if payment.status != Payment.Status.SUBMITTED:
        raise WorkflowConflict(_("Only submitted payments can be reviewed."))
    if (
        payment.method == Payment.Method.CASH_ON_DELIVERY
        and order.status != PurchaseOrder.Status.CONFIRMED
    ):
        raise WorkflowConflict(
            _(
                "Cash-on-delivery payment can no longer be reviewed after order "
                "preparation has started."
            )
        )

    payment.status = target
    payment.admin_note = admin_note.strip()
    fields = ["status", "admin_note", "updated_at"]
    if target == Payment.Status.CONFIRMED:
        payment.confirmed_at = timezone.now()
        payment.confirmed_by = actor
        fields.extend(["confirmed_at", "confirmed_by"])
    payment.save(update_fields=fields)

    kind = (
        Notification.Kind.PAYMENT_CONFIRMED
        if target == Payment.Status.CONFIRMED
        else Notification.Kind.PAYMENT_REJECTED
    )
    for recipient in (order.owner, order.supplier):
        _notify(
            user=recipient,
            actor=actor,
            kind=kind,
            event_key=f"payment-{target}:{payment.pk}:{recipient.pk}",
            rfq=order.rfq,
            order=order,
        )
    return payment, True


@transaction.atomic
def create_review(*, order_id, actor, rating, comment=""):
    order = (
        PurchaseOrder.objects.select_for_update()
        .select_related("rfq__owner", "quote__supplier")
        .get(pk=order_id)
    )
    if actor != order.owner:
        raise PermissionDenied(_("Only the order owner can review the supplier."))
    if order.status != PurchaseOrder.Status.COMPLETED:
        raise WorkflowConflict(_("A review can be submitted only after order completion."))
    if Review.objects.filter(order=order).exists():
        raise WorkflowConflict(_("This order has already been reviewed."))

    review = Review.objects.create(
        order=order,
        rating=rating,
        comment=comment.strip(),
    )
    _notify(
        user=order.supplier,
        actor=actor,
        kind=Notification.Kind.REVIEW_RECEIVED,
        event_key=f"review-received:{review.pk}:{order.supplier.pk}",
        rfq=order.rfq,
        order=order,
    )
    return review
