from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import Company, User, RoleAssignment, ApproverStage, ApprovalPolicy, Expense, ApprovalStep

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Company/Role", {"fields": ("company", "role", "manager")}),
    )
    list_display = ("username", "email", "company", "role", "manager", "is_staff")

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "country_code", "currency_code", "is_manager_first_approver")

@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("company", "role_name", "user")

@admin.register(ApproverStage)
class ApproverStageAdmin(admin.ModelAdmin):
    list_display = ("company", "sequence", "name", "role_name", "specific_user")
    ordering = ("company", "sequence")

@admin.register(ApprovalPolicy)
class ApprovalPolicyAdmin(admin.ModelAdmin):
    list_display = ("company", "mode", "percentage_required", "specific_approver")

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("id", "submitter", "company", "amount", "currency_code", "amount_converted", "status", "expense_date")

@admin.register(ApprovalStep)
class ApprovalStepAdmin(admin.ModelAdmin):
    list_display = ("expense", "sequence", "approver", "status", "acted_at")
    ordering = ("expense", "sequence")
