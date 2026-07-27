from django.urls import path

from . import views

urlpatterns = [
    path("", views.rfq_list, name="rfq_list"),
    path("new/", views.rfq_create, name="rfq_create"),
    path("catalog/", views.product_list, name="product_list"),
    path("catalog/mine/", views.my_products, name="my_products"),
    path("catalog/new/", views.product_create, name="product_create"),
    path("catalog/<int:pk>/", views.product_detail, name="product_detail"),
    path("catalog/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("catalog/<int:pk>/toggle/", views.product_toggle, name="product_toggle"),
    path("suppliers/<int:pk>/", views.supplier_profile, name="supplier_profile"),
    path("quotes/mine/", views.my_quotes, name="my_quotes"),
    path("quotes/<int:pk>/edit/", views.quote_edit, name="quote_edit"),
    path("quotes/<int:pk>/withdraw/", views.quote_withdraw, name="quote_withdraw"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/status/", views.order_update_status, name="order_update_status"),
    path("orders/<int:pk>/payment/", views.payment_submit, name="payment_submit"),
    path("orders/<int:pk>/review/", views.review_create, name="review_create"),
    path("payments/<int:payment_pk>/review/", views.payment_review, name="payment_review"),
    path("notifications/", views.notification_list, name="notification_list"),
    path("notifications/read-all/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("notifications/<int:pk>/open/", views.notification_open, name="notification_open"),
    path("reports/", views.reports, name="reports"),
    path("reports/export.csv", views.reports_export_csv, name="reports_export_csv"),
    path("quotes/<int:quote_pk>/award/", views.quote_award, name="quote_award"),
    path("<int:pk>/compare/", views.rfq_compare, name="rfq_compare"),
    path("<int:pk>/", views.rfq_detail, name="rfq_detail"),
    path("<int:pk>/quote/", views.quote_create, name="quote_create"),
]
