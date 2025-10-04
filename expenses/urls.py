from django.urls import path
from . import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("settings/company/", views.company_settings, name="company_settings"),
    path("settings/policy/", views.policy_settings, name="policy_settings"),
    path("settings/stages/", views.manage_stages, name="manage_stages"),
    path("settings/stages/<int:stage_id>/delete/", views.delete_stage, name="delete_stage"),
    path("settings/roles/", views.assign_roles, name="assign_roles"),

    path("users/", views.users_list, name="users_list"),
    path("users/new/", views.user_new, name="user_new"),
    path("users/<int:user_id>/edit/", views.user_edit, name="user_edit"),

    path("new/", views.expense_create, name="expense_create"),
    path("mine/", views.my_expenses, name="my_expenses"),
    path("approvals/", views.approvals_queue, name="approvals_queue"),
    path("<int:expense_id>/approve/", views.approve_expense_view, name="approve_expense"),
    path("<int:expense_id>/reject/", views.reject_expense_view, name="reject_expense"),

    path("admin/expenses/", views.admin_expenses, name="admin_expenses"),
    path("admin/expenses/<int:expense_id>/override/approve/", views.admin_override_approve, name="admin_override_approve"),
    path("admin/expenses/<int:expense_id>/override/reject/", views.admin_override_reject, name="admin_override_reject"),
]
