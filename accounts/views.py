from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from .forms import CompanyProfileForm, RegisterForm
from .models import CompanyProfile


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    initial = {}
    if request.GET.get("role") in {"owner", "supplier"}:
        initial["role"] = request.GET["role"]

    form = RegisterForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(
                "email",
                _("An account with this email address already exists."),
            )
        else:
            login(request, user)
            messages.success(
                request,
                _("Welcome! Your account has been created successfully."),
            )
            return redirect("dashboard")
    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    company, _ = CompanyProfile.objects.get_or_create(
        user=request.user,
        defaults={"trade_name": request.user.username},
    )
    return render(
        request,
        "accounts/profile.html",
        {
            "company": company,
            "product_count": request.user.products.count() if request.user.is_supplier else 0,
        },
    )


@login_required
def profile_edit(request):
    company, _ = CompanyProfile.objects.get_or_create(
        user=request.user,
        defaults={"trade_name": request.user.username},
    )
    form = CompanyProfileForm(
        request.POST or None,
        request.FILES or None,
        instance=company,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        if form.verification_was_reset:
            messages.warning(
                request,
                _(
                    "Your organization profile was updated and returned to verification review because official information changed."
                ),
            )
        else:
            messages.success(
                request,
                _("Your organization profile was updated successfully."),
            )
        return redirect("profile")
    return render(
        request,
        "accounts/profile_form.html",
        {"form": form, "company": company},
    )


@login_required
def company_document_download(request, pk):
    company = get_object_or_404(CompanyProfile, pk=pk)
    if request.user != company.user and not request.user.is_staff:
        raise Http404
    if not company.registration_document:
        raise Http404
    response = FileResponse(
        company.registration_document.open("rb"),
        as_attachment=True,
        filename=Path(company.registration_document.name).name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
