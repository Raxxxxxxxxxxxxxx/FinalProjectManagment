from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Payment, Product, Quote, Review, RFQ, RFQItem, Unit


INPUT_CLASS = (
    "mt-2 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 "
    "text-slate-900 outline-none transition focus:border-gold-500 focus:ring-2 "
    "focus:ring-gold-100"
)


class StyledFormMixin:
    def apply_styles(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = (
                    "h-5 w-5 rounded border-slate-300 text-university-700 "
                    "focus:ring-gold-500"
                )
            else:
                field.widget.attrs["class"] = INPUT_CLASS


class RFQForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = RFQ
        fields = (
            "title",
            "category",
            "description",
            "delivery_city",
            "deadline",
            "budget_min",
            "budget_max",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "deadline": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].label_from_instance = lambda category: category.display_name
        self.fields["budget_min"].label = _("Minimum budget (USD)")
        self.fields["budget_max"].label = _("Maximum budget (USD)")
        self.apply_styles()

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("budget_min")
        maximum = cleaned.get("budget_max")
        deadline = cleaned.get("deadline")
        if minimum and maximum and minimum > maximum:
            self.add_error("budget_max", _("The maximum budget must be greater than the minimum budget."))
        if deadline and deadline < timezone.localdate():
            self.add_error("deadline", _("The deadline must be today or a later date."))
        return cleaned


class RFQItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = RFQItem
        fields = ("name", "quantity", "unit", "specifications")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields["unit"].initial = Unit.PIECE
        self.apply_styles()


RFQItemFormSet = inlineformset_factory(
    RFQ,
    RFQItem,
    form=RFQItemForm,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class QuoteForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Quote
        fields = ("total_amount", "delivery_days", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total_amount"].label = _("Total quote (USD)")
        self.apply_styles()


class ProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = (
            "category",
            "sku",
            "name",
            "description",
            "unit",
            "minimum_order_quantity",
            "price",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].label_from_instance = lambda category: category.display_name
        self.fields["price"].label = _("Reference price (USD)")
        self.apply_styles()


class OrderProgressForm(StyledFormMixin, forms.Form):
    note = forms.CharField(
        label=_("Progress note"),
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    tracking_reference = forms.CharField(
        label=_("Tracking reference"),
        required=False,
        max_length=120,
    )

    def __init__(self, *args, show_tracking=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not show_tracking:
            self.fields.pop("tracking_reference")
        self.apply_styles()


class PaymentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Payment
        fields = ("method", "reference", "note")
        widgets = {"note": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("method") == Payment.Method.BANK_TRANSFER
            and not cleaned.get("reference", "").strip()
        ):
            self.add_error("reference", _("A transfer reference is required for bank transfers."))
        return cleaned


class PaymentDecisionForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(
        label=_("Decision"),
        choices=[
            ("confirm", _("Confirm payment")),
            ("reject", _("Reject payment")),
        ],
    )
    admin_note = forms.CharField(
        label=_("Administration note"),
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "reject" and not cleaned.get("admin_note", "").strip():
            self.add_error("admin_note", _("A reason is required when rejecting payment details."))
        return cleaned


class ReviewForm(StyledFormMixin, forms.ModelForm):
    rating = forms.TypedChoiceField(
        label=_("Overall rating"),
        choices=[(value, _("%(value)s out of 5") % {"value": value}) for value in range(5, 0, -1)],
        coerce=int,
    )

    class Meta:
        model = Review
        fields = ("rating", "comment")
        labels = {"comment": _("Review comment")}
        widgets = {"comment": forms.Textarea(attrs={"rows": 5, "maxlength": 2000})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["comment"].required = False
        self.apply_styles()
