from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import CompanyProfile, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (_("Platform information"), {"fields": ("role", "phone", "email_verified")}),
    )
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("trade_name", "user", "business_type", "city", "is_verified")
    list_filter = ("is_verified", "city")
    search_fields = ("trade_name", "user__username", "registration_number")
    actions = ("verify_selected", "unverify_selected")

    @admin.action(description=_("Verify selected organization profiles"))
    def verify_selected(self, request, queryset):
        eligible = queryset.exclude(registration_number="").exclude(
            registration_document=""
        )
        updated = eligible.update(is_verified=True, updated_at=timezone.now())
        skipped = queryset.count() - updated
        self.message_user(
            request,
            _("%(count)s organization profile(s) verified.") % {"count": updated},
            level=messages.SUCCESS,
        )
        if skipped:
            self.message_user(
                request,
                _(
                    "%(count)s profile(s) skipped because the registration number or document is missing."
                )
                % {"count": skipped},
                level=messages.WARNING,
            )

    @admin.action(description=_("Remove verification from selected profiles"))
    def unverify_selected(self, request, queryset):
        updated = queryset.update(is_verified=False, updated_at=timezone.now())
        self.message_user(
            request,
            _("%(count)s organization profile(s) marked as unverified.")
            % {"count": updated},
            level=messages.SUCCESS,
        )
