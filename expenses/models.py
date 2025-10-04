from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

class Company(models.Model):
    name = models.CharField(max_length=255)
    country_code = models.CharField(max_length=4)
    currency_code = models.CharField(max_length=8)
    is_manager_first_approver = models.BooleanField(default=True)

    def __str__(self):
        return self.name

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MANAGER = "MANAGER", "Manager"
        EMPLOYEE = "EMPLOYEE", "Employee"

    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="users")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.EMPLOYEE)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="team_members")

    def is_admin(self):
        return self.role == self.Role.ADMIN

    def is_manager(self):
        return self.role == self.Role.MANAGER

    def is_employee(self):
        return self.role == self.Role.EMPLOYEE

    def __str__(self):
        return f"{self.username} ({self.role})"

class ApproverRole(models.TextChoices):
    MANAGER = "MANAGER", "Manager"
    FINANCE = "FINANCE", "Finance"
    DIRECTOR = "DIRECTOR", "Director"
    CFO = "CFO", "CFO"

class RoleAssignment(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="role_assignments")
    role_name = models.CharField(max_length=32, choices=ApproverRole.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("company", "role_name")

    def __str__(self):
        return f"{self.company} - {self.role_name} -> {self.user}"

class ApprovalPolicy(models.Model):
    class Mode(models.TextChoices):
        PERCENTAGE = "PERCENTAGE", "Percentage"
        SPECIFIC = "SPECIFIC", "Specific Approver"
        PERCENTAGE_OR_SPECIFIC = "PERCENTAGE_OR_SPECIFIC", "Percentage OR Specific"

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name="approval_policy")
    percentage_required = models.PositiveIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    specific_approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.PERCENTAGE)

    def __str__(self):
        return f"Policy for {self.company}"

class ApproverStage(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="approver_stages")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=64, default="Stage")
    # Either specify a role or a specific user for this stage
    role_name = models.CharField(max_length=32, choices=ApproverRole.choices, null=True, blank=True)
    specific_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["sequence"]

    def __str__(self):
        who = self.specific_user or self.role_name or "Unassigned"
        return f"{self.company} Stage {self.sequence}: {self.name} ({who})"

class Expense(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    submitter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="expenses")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="expenses")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency_code = models.CharField(max_length=8)
    amount_converted = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Amount in company currency")
    category = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    expense_date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    receipt = models.ImageField(upload_to="receipts/", null=True, blank=True)
    merchant_name = models.CharField(max_length=255, blank=True)
    ocr_text = models.TextField(blank=True)

    def __str__(self):
        return f"Expense #{self.id} by {self.submitter} - {self.amount} {self.currency_code}"

class ApprovalStep(models.Model):
    class StepStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        SKIPPED = "SKIPPED", "Skipped"

    expense = models.ForeignKey(Expense, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveIntegerField()
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=StepStatus.choices, default=StepStatus.PENDING)
    comment = models.TextField(blank=True)
    acted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]

    def __str__(self):
        return f"Expense {self.expense_id} Step {self.sequence} -> {self.approver} [{self.status}]"
