from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .models import CompanyProfile, User


INPUT_CLASS = (
    "mt-2 w-full rounded-xl border border-slate-200 bg-white px-4 py-3 "
    "text-slate-900 outline-none transition focus:border-gold-500 focus:ring-2 "
    "focus:ring-gold-100"
)


class RegisterForm(UserCreationForm):
    trade_name = forms.CharField(label=_("Organization name"), max_length=180)
    email = forms.EmailField(label=_("Email address"), required=True)
    phone = forms.CharField(label=_("Mobile number"), max_length=30)
    role = forms.ChoiceField(
        label=_("Account type"),
        choices=[
            (User.Role.OWNER, _("Project owner")),
            (User.Role.SUPPLIER, _("Supplier")),
        ],
    )

    class Meta:
        model = User
        fields = ("username", "email", "phone", "trade_name", "role", "password1", "password2")
        labels = {"username": _("Username")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("An account with this email address already exists."))
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.phone = self.cleaned_data["phone"]
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
            CompanyProfile.objects.create(
                user=user,
                trade_name=self.cleaned_data["trade_name"],
            )
        return user


class CompanyProfileForm(forms.ModelForm):
    verification_fields = {
        "trade_name",
        "registration_number",
        "registration_document",
    }

    class Meta:
        model = CompanyProfile
        fields = (
            "trade_name",
            "business_type",
            "city",
            "description",
            "registration_number",
            "registration_document",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS
        self.fields["registration_document"].help_text = _(
            "PDF, JPG, or PNG. Maximum file size: 5 MB."
        )

    def clean_registration_document(self):
        document = self.cleaned_data.get("registration_document")
        if not document:
            return document
        if document.size > 5 * 1024 * 1024:
            raise forms.ValidationError(
                _("The registration document must not exceed 5 MB.")
            )

        signature = document.read(12)
        document.seek(0)
        allowed_signatures = (
            signature.startswith(b"%PDF-"),
            signature.startswith(b"\x89PNG\r\n\x1a\n"),
            signature.startswith(b"\xff\xd8\xff"),
        )
        if not any(allowed_signatures):
            raise forms.ValidationError(
                _("The uploaded file content does not match an allowed PDF, JPG, or PNG document.")
            )
        return document

    def save(self, commit=True):
        candidate = super().save(commit=False)
        self.verification_was_reset = False
        if not commit:
            return candidate
        if not candidate.pk:
            candidate.is_verified = False
            candidate.save()
            self.save_m2m()
            self.instance = candidate
            return candidate

        with transaction.atomic():
            company = CompanyProfile.objects.select_for_update().get(pk=candidate.pk)
            original_document_name = (
                company.registration_document.name
                if company.registration_document
                else ""
            )
            changed_fields = [
                field_name
                for field_name in self.Meta.fields
                if field_name in self.changed_data
            ]
            for field_name in changed_fields:
                model_field = company._meta.get_field(field_name)
                model_field.save_form_data(
                    company,
                    self.cleaned_data.get(field_name),
                )

            official_information_changed = bool(
                self.verification_fields.intersection(changed_fields)
            )
            self.verification_was_reset = bool(
                company.is_verified and official_information_changed
            )
            if official_information_changed:
                company.is_verified = False

            update_fields = [*changed_fields, "updated_at"]
            if official_information_changed:
                update_fields.append("is_verified")
            if changed_fields or official_information_changed:
                company.save(update_fields=list(dict.fromkeys(update_fields)))

            current_document_name = (
                company.registration_document.name
                if company.registration_document
                else ""
            )
            if (
                original_document_name
                and original_document_name != current_document_name
            ):
                storage = company.registration_document.storage
                transaction.on_commit(
                    lambda name=original_document_name, backend=storage: backend.delete(
                        name
                    )
                )
            self.instance = company
        return company
