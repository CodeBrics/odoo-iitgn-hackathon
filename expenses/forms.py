from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import Expense, User, Company, ApprovalPolicy, ApproverStage, ApproverRole

class SignupForm(forms.Form):
    company_name = forms.CharField(max_length=255)
    country_code = forms.CharField(max_length=3, help_text="ISO country code, e.g., US, IN, GB")
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["amount", "currency_code", "category", "description", "expense_date", "receipt"]

class ApprovalActionForm(forms.Form):
    comment = forms.CharField(widget=forms.Textarea, required=False)

class CompanySettingsForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["is_manager_first_approver"]

class ApprovalPolicyForm(forms.ModelForm):
    class Meta:
        model = ApprovalPolicy
        fields = ["mode", "percentage_required", "specific_approver"]

class ApproverStageForm(forms.ModelForm):
    class Meta:
        model = ApproverStage
        fields = ["sequence", "name", "role_name", "specific_user"]

class RoleAssignmentForm(forms.Form):
    role_name = forms.ChoiceField(choices=ApproverRole.choices)
    user = forms.ModelChoiceField(queryset=User.objects.none())

    def __init__(self, company, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(company=company)

class CreateUserForm(forms.Form):
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=User.Role.choices)
    manager = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    def __init__(self, company, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = User.objects.filter(company=company, role=User.Role.MANAGER)

class UpdateUserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["email", "role", "manager", "password"]

    def __init__(self, company, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = User.objects.filter(company=company, role=User.Role.MANAGER)
