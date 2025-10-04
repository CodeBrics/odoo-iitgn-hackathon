from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import (
    SignupForm, ExpenseForm, ApprovalActionForm,
    CompanySettingsForm, ApprovalPolicyForm, ApproverStageForm, RoleAssignmentForm,
    CreateUserForm, UpdateUserForm,  # add user management forms
)
from .models import Company, User, Expense, ApprovalStep, ApproverStage, RoleAssignment, ApproverRole
from .services import get_currency_for_country, convert_amount, build_approval_steps_for_expense, approve_step, reject_expense
from .services import admin_override_expense, ocr_extract  # admin override and OCR helper
from django.views.decorators.http import require_http_methods

class LoginViewCustom(LoginView):
    template_name = "expenses/login.html"

def logout_view(request: HttpRequest):
    logout(request)
    return redirect("login")

def is_admin(user: User): return user.is_authenticated and user.is_admin()
def is_manager(user: User): return user.is_authenticated and user.is_manager()
def is_employee(user: User): return user.is_authenticated and user.is_employee()

def signup(request: HttpRequest):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            company_name = form.cleaned_data["company_name"]
            country_code = form.cleaned_data["country_code"]
            username = form.cleaned_data["username"]
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]

            currency = get_currency_for_country(country_code) or "USD"
            company = Company.objects.create(
                name=company_name,
                country_code=country_code.upper(),
                currency_code=currency,
                is_manager_first_approver=True,
            )
            user = User.objects.create_user(
                username=username, email=email, password=password, company=company, role=User.Role.ADMIN
            )
            login(request, user)
            messages.success(request, f"Company '{company.name}' created with currency {company.currency_code}.")
            return redirect("dashboard")
    else:
        form = SignupForm()
    return render(request, "expenses/signup.html", {"form": form})

@login_required
def dashboard(request: HttpRequest):
    user: User = request.user
    context = {}

    if user.is_admin():
        context["admin_stats"] = {
            "users": user.company.users.count() if user.company else 0,
            "expenses": user.company.expenses.count() if user.company else 0,
            "pending": ApprovalStep.objects.filter(expense__company=user.company, status=ApprovalStep.StepStatus.PENDING).count() if user.company else 0,
        }
        template = "expenses/dashboard_admin.html"
    elif user.is_manager():
        pending = ApprovalStep.objects.filter(approver=user, status=ApprovalStep.StepStatus.PENDING).select_related("expense")
        context["pending"] = pending
        template = "expenses/dashboard_manager.html"
    else:
        # employee
        my = Expense.objects.filter(submitter=user).order_by("-created_at")
        context["my_expenses"] = my
        template = "expenses/dashboard_employee.html"
    return render(request, template, context)

@login_required
@user_passes_test(is_employee)
def expense_create(request: HttpRequest):
    user: User = request.user
    if request.method == "POST":
        action = request.POST.get("action", "submit")
        if action == "autofill" and request.FILES.get("receipt"):
            # Try to OCR and re-render the form pre-filled
            extracted = ocr_extract(request.FILES["receipt"])
            initial = {
                "description": extracted.get("description") or "",
                "category": "Meals" if "restaurant" in (extracted.get("description") or "").lower() else "",
            }
            if extracted.get("amount"):
                initial["amount"] = extracted["amount"]
            if extracted.get("date"):
                initial["expense_date"] = extracted["date"]
            form = ExpenseForm(initial=initial)
            messages.info(request, "Receipt processed. Please verify and submit.")
            return render(request, "expenses/expense_form.html", {"form": form, "company_currency": user.company.currency_code})

        form = ExpenseForm(request.POST, request.FILES)
        if form.is_valid():
            exp: Expense = form.save(commit=False)
            exp.submitter = user
            exp.company = user.company
            # Convert amount to company currency
            converted = convert_amount(Decimal(exp.amount), exp.currency_code, user.company.currency_code)
            exp.amount_converted = converted if converted is not None else exp.amount
            exp.status = Expense.Status.DRAFT
            exp.save()
            # Persist OCR info if present
            if exp.receipt and not exp.ocr_text:
                # best effort: we won't re-OCR the saved file here, only store merchant if provided via POST
                pass
            # Build steps & set pending/approved
            build_approval_steps_for_expense(exp)
            messages.success(request, f"Expense submitted with status {exp.status}.")
            return redirect("my_expenses")
    else:
        form = ExpenseForm()
    return render(request, "expenses/expense_form.html", {"form": form, "company_currency": request.user.company.currency_code})

@login_required
@user_passes_test(is_employee)
def my_expenses(request: HttpRequest):
    qs = Expense.objects.filter(submitter=request.user).order_by("-created_at")
    return render(request, "expenses/expense_list.html", {"expenses": qs})

@login_required
@user_passes_test(is_manager)
def approvals_queue(request: HttpRequest):
    pending = ApprovalStep.objects.filter(approver=request.user, status=ApprovalStep.StepStatus.PENDING).select_related("expense")
    return render(request, "expenses/approvals_queue.html", {"pending": pending})

@login_required
@user_passes_test(is_manager)
def approve_expense_view(request: HttpRequest, expense_id: int):
    step = get_object_or_404(ApprovalStep, expense_id=expense_id, approver=request.user, status=ApprovalStep.StepStatus.PENDING)
    if request.method == "POST":
        form = ApprovalActionForm(request.POST)
        if form.is_valid():
            approve_step(step.expense, request.user, form.cleaned_data.get("comment", ""))
            messages.success(request, "Approved.")
            return redirect("approvals_queue")
    else:
        form = ApprovalActionForm()
    return render(request, "expenses/approval_action.html", {"form": form, "expense": step.expense, "action": "Approve"})

@login_required
@user_passes_test(is_manager)
def reject_expense_view(request: HttpRequest, expense_id: int):
    step = get_object_or_404(ApprovalStep, expense_id=expense_id, approver=request.user, status=ApprovalStep.StepStatus.PENDING)
    if request.method == "POST":
        form = ApprovalActionForm(request.POST)
        if form.is_valid():
            reject_expense(step.expense, request.user, form.cleaned_data.get("comment", ""))
            messages.info(request, "Rejected.")
            return redirect("approvals_queue")
    else:
        form = ApprovalActionForm()
    return render(request, "expenses/approval_action.html", {"form": form, "expense": step.expense, "action": "Reject"})

@login_required
@user_passes_test(is_admin)
def company_settings(request: HttpRequest):
    company = request.user.company
    if request.method == "POST":
        form = CompanySettingsForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Company settings updated.")
            return redirect("company_settings")
    else:
        form = CompanySettingsForm(instance=company)
    return render(request, "expenses/company_settings.html", {"form": form})

@login_required
@user_passes_test(is_admin)
def policy_settings(request: HttpRequest):
    company = request.user.company
    if not company:
        # Fallback for superusers or users not linked to a company
        company = Company.objects.first()
        if not company:
            messages.error(request, "No company found. Please create a company via signup.")
            return redirect("dashboard")

    from .models import ApprovalPolicy, User
    # Always bind the policy to the current company; avoids NoneType and NOT NULL errors
    policy, _ = ApprovalPolicy.objects.get_or_create(company=company)

    if request.method == "POST":
        form = ApprovalPolicyForm(request.POST, instance=policy)
        # Limit approver choices to current company users
        form.fields["specific_approver"].queryset = User.objects.filter(company=company)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.company = company  # ensure company is always set
            obj.save()
            messages.success(request, "Approval policy updated.")
            return redirect("policy_settings")
    else:
        form = ApprovalPolicyForm(instance=policy)
        form.fields["specific_approver"].queryset = User.objects.filter(company=company)
    return render(request, "expenses/policy_settings.html", {"form": form})

@login_required
@user_passes_test(is_admin)
def manage_stages(request: HttpRequest):
    company = request.user.company
    if not company:
        # Fallback for superusers or users not linked to a company
        company = Company.objects.first()
        if not company:
            messages.error(request, "No company found. Please create a company via signup.")
            return redirect("dashboard")

    if request.method == "POST":
        form = ApproverStageForm(request.POST)
        form.fields["specific_user"].queryset = User.objects.filter(company=company)
        if form.is_valid():
            stage = form.save(commit=False)
            stage.company = company
            stage.save()
            messages.success(request, "Stage added.")
            return redirect("manage_stages")
    else:
        form = ApproverStageForm()
        form.fields["specific_user"].queryset = User.objects.filter(company=company)
    stages = company.approver_stages.all()
    return render(request, "expenses/manage_stages.html", {"form": form, "stages": stages})

@login_required
@user_passes_test(is_admin)
def delete_stage(request: HttpRequest, stage_id: int):
    stage = get_object_or_404(ApproverStage, id=stage_id, company=request.user.company)
    stage.delete()
    messages.info(request, "Stage deleted.")
    return redirect("manage_stages")

@login_required
@user_passes_test(is_admin)
def users_list(request: HttpRequest):
    company = request.user.company
    users = User.objects.filter(company=company).order_by("username")
    return render(request, "expenses/users_list.html", {"users": users})

@login_required
@user_passes_test(is_admin)
def user_new(request: HttpRequest):
    company = request.user.company
    if request.method == "POST":
        form = CreateUserForm(company, request.POST)
        if form.is_valid():
            user = User(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                company=company,
                role=form.cleaned_data["role"],
                manager=form.cleaned_data.get("manager"),
            )
            user.set_password(form.cleaned_data["password"])
            user.save()
            messages.success(request, f"User {user.username} created.")
            return redirect("users_list")
    else:
        form = CreateUserForm(company)
    return render(request, "expenses/user_form.html", {"form": form, "title": "Create User"})

@login_required
@user_passes_test(is_admin)
def user_edit(request: HttpRequest, user_id: int):
    company = request.user.company
    user = get_object_or_404(User, id=user_id, company=company)
    if request.method == "POST":
        form = UpdateUserForm(company, request.POST, instance=user)
        if form.is_valid():
            u = form.save(commit=False)
            pwd = form.cleaned_data.get("password")
            if pwd:
                u.set_password(pwd)
            u.save()
            messages.success(request, f"User {u.username} updated.")
            return redirect("users_list")
    else:
        form = UpdateUserForm(company, instance=user)
    return render(request, "expenses/user_form.html", {"form": form, "title": f"Edit {user.username}"})

@login_required
@user_passes_test(is_admin)
def admin_expenses(request: HttpRequest):
    qs = Expense.objects.filter(company=request.user.company).select_related("submitter").order_by("-created_at")
    return render(request, "expenses/admin_expenses.html", {"expenses": qs})

@login_required
@user_passes_test(is_admin)
def admin_override_approve(request: HttpRequest, expense_id: int):
    expense = get_object_or_404(Expense, id=expense_id, company=request.user.company)
    admin_override_expense(expense, Expense.Status.APPROVED, comment="Approved by admin override.")
    messages.success(request, "Expense approved by admin override.")
    return redirect("admin_expenses")

@login_required
@user_passes_test(is_admin)
def admin_override_reject(request: HttpRequest, expense_id: int):
    expense = get_object_or_404(Expense, id=expense_id, company=request.user.company)
    admin_override_expense(expense, Expense.Status.REJECTED, comment="Rejected by admin override.")
    messages.info(request, "Expense rejected by admin override.")
    return redirect("admin_expenses")

@login_required
@user_passes_test(is_admin)
@require_http_methods(["GET", "POST"])
def assign_roles(request: HttpRequest):
    """
    Admin can map company-level approver roles (FINANCE, DIRECTOR, CFO, etc.) to specific users.
    Uses update_or_create to ensure one assignment per (company, role_name).
    """
    company = request.user.company
    if company is None:
        messages.error(request, "No company found for your user.")
        return redirect("dashboard")

    # Bind the form to the current company so user choices are limited correctly.
    if request.method == "POST":
        form = RoleAssignmentForm(company, request.POST)
        if form.is_valid():
            role_name = form.cleaned_data["role_name"]
            user = form.cleaned_data["user"]
            # Ensure uniqueness per role within the company
            RoleAssignment.objects.update_or_create(
                company=company,
                role_name=role_name,
                defaults={"user": user},
            )
            messages.success(request, f"Assigned {role_name} role to {user.username}.")
            return redirect("assign_roles")
    else:
        form = RoleAssignmentForm(company)

    assignments = RoleAssignment.objects.filter(company=company).select_related("user").order_by("role_name")
    return render(request, "expenses/assign_roles.html", {"form": form, "assignments": assignments})
