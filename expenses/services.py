import decimal
from typing import Optional, List
import requests
from django.utils import timezone
from django.core.cache import cache
from django.db import transaction
import base64
import re
import os

from .models import (
    Company,
    User,
    RoleAssignment,
    ApproverStage,
    ApprovalPolicy,
    Expense,
    ApprovalStep,
    ApproverRole,
)

REST_COUNTRIES_URL = "https://restcountries.com/v3.1/all?fields=name,currencies,cca2,cca3,cioc,cca2"
EXCHANGE_RATE_URL = "https://api.exchangerate-api.com/v4/latest/{base}"

def get_currency_for_country(country_code: str) -> Optional[str]:
    """Return primary currency code for an ISO country code (e.g., 'US' -> 'USD')."""
    country_code = (country_code or "").strip().upper()
    cache_key = f"restcountries:{country_code}"
    code = cache.get(cache_key)
    if code:
        return code

    try:
        resp = requests.get(REST_COUNTRIES_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for entry in data:
            # country codes vary (cca2, cca3), try common fields
            cca2 = (entry.get("cca2") or "").upper()
            if cca2 == country_code:
                currencies = entry.get("currencies") or {}
                if currencies:
                    code = list(currencies.keys())[0]
                    cache.set(cache_key, code, 60 * 60 * 24)
                    return code
    except Exception:
        return None
    return None

def convert_amount(amount: decimal.Decimal, from_ccy: str, to_ccy: str) -> Optional[decimal.Decimal]:
    """Convert amount using public exchange rate API."""
    from_ccy = (from_ccy or "").upper()
    to_ccy = (to_ccy or "").upper()
    if not amount or from_ccy == to_ccy:
        return amount

    cache_key = f"fx:{from_ccy}"
    rates = cache.get(cache_key)
    if not rates:
        try:
            resp = requests.get(EXCHANGE_RATE_URL.format(base=from_ccy), timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            rates = payload.get("rates")
            if not rates:
                return None
            cache.set(cache_key, rates, 60 * 60)  # 1 hour
        except Exception:
            return None

    rate = rates.get(to_ccy)
    if not rate:
        return None
    try:
        return (decimal.Decimal(rate) * amount).quantize(decimal.Decimal("0.01"))
    except Exception:
        return None

def resolve_stage_assignee(company: Company, stage: ApproverStage, submitter: User) -> Optional[User]:
    """Determine which user should approve a stage."""
    if stage.specific_user:
        return stage.specific_user
    if stage.role_name:
        # Special case: Manager role is dynamic per submitter
        if stage.role_name == ApproverRole.MANAGER:
            return submitter.manager
        try:
            ra = RoleAssignment.objects.get(company=company, role_name=stage.role_name)
            return ra.user
        except RoleAssignment.DoesNotExist:
            return None
    return None

@transaction.atomic
def build_approval_steps_for_expense(expense: Expense):
    """Create all approval steps (manager first if configured) and configured stages."""
    company = expense.company
    submitter = expense.submitter
    seq = 1

    # Manager-first stage if enabled
    if company.is_manager_first_approver and submitter.manager:
        ApprovalStep.objects.create(
            expense=expense, sequence=seq, approver=submitter.manager
        )
        seq += 1

    # Configured stages
    for stage in company.approver_stages.all().order_by("sequence"):
        assignee = resolve_stage_assignee(company, stage, submitter)
        if assignee:
            ApprovalStep.objects.create(expense=expense, sequence=seq, approver=assignee)
            seq += 1

    # If no steps, auto approve
    if expense.steps.count() == 0:
        expense.status = Expense.Status.APPROVED
        expense.save(update_fields=["status"])
    else:
        expense.status = Expense.Status.PENDING
        expense.save(update_fields=["status"])

def evaluate_policy_and_maybe_finalize(expense: Expense):
    """Evaluate conditional policy and mark expense approved if conditions are met."""
    try:
        policy = expense.company.approval_policy
    except ApprovalPolicy.DoesNotExist:
        return  # No policy configured

    steps = list(expense.steps.all())
    if not steps:
        return

    approved = [s for s in steps if s.status == ApprovalStep.StepStatus.APPROVED]
    total = len(steps)

    def finalize_approved():
        # Mark remaining steps as skipped and set expense to APPROVED
        now = timezone.now()
        for s in steps:
            if s.status == ApprovalStep.StepStatus.PENDING:
                s.status = ApprovalStep.StepStatus.SKIPPED
                s.acted_at = now
                s.save(update_fields=["status", "acted_at"])
        expense.status = Expense.Status.APPROVED
        expense.save(update_fields=["status"])

    if policy.mode == ApprovalPolicy.Mode.SPECIFIC and policy.specific_approver:
        if any(s.approver_id == policy.specific_approver_id and s.status == ApprovalStep.StepStatus.APPROVED for s in steps):
            finalize_approved()
            return

    if policy.mode == ApprovalPolicy.Mode.PERCENTAGE and policy.percentage_required > 0:
        if total > 0 and (len(approved) * 100 / total) >= policy.percentage_required:
            finalize_approved()
            return

    if policy.mode == ApprovalPolicy.Mode.PERCENTAGE_OR_SPECIFIC:
        specific_ok = policy.specific_approver and any(
            s.approver_id == policy.specific_approver_id and s.status == ApprovalStep.StepStatus.APPROVED for s in steps
        )
        percentage_ok = total > 0 and policy.percentage_required > 0 and (len(approved) * 100 / total) >= policy.percentage_required
        if specific_ok or percentage_ok:
            finalize_approved()

@transaction.atomic
def approve_step(expense: Expense, user: User, comment: str = "") -> bool:
    """Approve the current pending step for a given user, move to next step, or finalize."""
    if expense.status not in (Expense.Status.PENDING,):
        return False

    step = expense.steps.filter(approver=user, status=ApprovalStep.StepStatus.PENDING).order_by("sequence").first()
    if not step:
        return False

    step.status = ApprovalStep.StepStatus.APPROVED
    step.comment = comment or ""
    step.acted_at = timezone.now()
    step.save(update_fields=["status", "comment", "acted_at"])

    # Evaluate conditional policy first
    evaluate_policy_and_maybe_finalize(expense)
    if expense.status == Expense.Status.APPROVED:
        return True

    # Otherwise advance to next step if any pending exists; if none left -> approve
    any_pending = expense.steps.filter(status=ApprovalStep.StepStatus.PENDING).exists()
    if not any_pending:
        expense.status = Expense.Status.APPROVED
        expense.save(update_fields=["status"])
    return True

@transaction.atomic
def reject_expense(expense: Expense, user: User, comment: str = "") -> bool:
    """Reject the expense and close remaining steps."""
    step = expense.steps.filter(approver=user, status=ApprovalStep.StepStatus.PENDING).order_by("sequence").first()
    if not step:
        return False

    step.status = ApprovalStep.StepStatus.REJECTED
    step.comment = comment or ""
    step.acted_at = timezone.now()
    step.save(update_fields=["status", "comment", "acted_at"])

    # Mark expense rejected
    expense.status = Expense.Status.REJECTED
    expense.save(update_fields=["status"])

    # Close remaining pending steps as skipped
    now = timezone.now()
    for s in expense.steps.filter(status=ApprovalStep.StepStatus.PENDING):
        s.status = ApprovalStep.StepStatus.SKIPPED if expense.status == Expense.Status.APPROVED else ApprovalStep.StepStatus.REJECTED
        s.comment = (s.comment or "") + (f"\n[Admin override] {comment}" if comment else "")
        s.acted_at = now
        s.save(update_fields=["status", "comment", "acted_at"])
    return True

@transaction.atomic
def admin_override_expense(expense: Expense, status: str, comment: str = "") -> bool:
    """
    Admin override: force approve/reject and close remaining steps.
    """
    if status not in (Expense.Status.APPROVED, Expense.Status.REJECTED):
        return False
    now = timezone.now()
    # close all pending steps
    for s in expense.steps.filter(status=ApprovalStep.StepStatus.PENDING):
        s.status = ApprovalStep.StepStatus.SKIPPED if status == Expense.Status.APPROVED else ApprovalStep.StepStatus.REJECTED
        s.comment = (s.comment or "") + (f"\n[Admin override] {comment}" if comment else "")
        s.acted_at = now
        s.save(update_fields=["status", "comment", "acted_at"])
    expense.status = status
    expense.save(update_fields=["status"])
    return True

def ocr_extract(file_obj) -> dict:
    """
    Extract text from a receipt image and try to infer amount, date, description, and merchant.
    Uses https://api.ocr.space if OCRSPACE_API_KEY is set; otherwise returns a minimal fallback.
    Returns a dict with possible keys: amount, date, description, merchant_name.
    """
    result = {"description": "", "merchant_name": ""}
    api_key = os.getenv("OCRSPACE_API_KEY")

    parsed_text = ""
    try:
        if api_key:
            # Call OCR.Space with multipart upload
            resp = requests.post(
                "https://api.ocr.space/parse/image",
                headers={"apikey": api_key},
                files={"file": file_obj},
                data={"OCREngine": 2, "scale": True, "isTable": False, "detectOrientation": True},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            parsed_results = data.get("ParsedResults") or []
            if parsed_results:
                parsed_text = parsed_results[0].get("ParsedText", "") or ""
        else:
            # No API key available; cannot truly OCR the image.
            parsed_text = ""
    except Exception:
        parsed_text = ""

    # Basic heuristics from parsed_text
    text = parsed_text.strip()
    result["description"] = (text[:500] or "").strip()

    # Merchant: first non-empty line in caps or title-cased
    merchant = ""
    for line in (text.splitlines() if text else []):
        ln = line.strip()
        if len(ln) >= 3:
            merchant = ln[:120]
            break
    result["merchant_name"] = merchant

    # Amount: find max monetary value pattern like 1234.56 or 1,234.56
    amount = None
    if text:
        candidates = re.findall(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))(?!\d)", text)
        def to_decimal(s):
            s = s.replace(",", "")
            try:
                return decimal.Decimal(s)
            except Exception:
                return None
        decs = [to_decimal(c) for c in candidates]
        decs = [d for d in decs if d is not None]
        if decs:
            amount = max(decs)
    if amount is not None:
        result["amount"] = amount

    # Date: simple patterns like 2025-10-03 or 03/10/2025
    date_val = None
    if text:
        date_patterns = [
            r"(\d{4}-\d{2}-\d{2})",
            r"(\d{2}/\d{2}/\d{4})",
            r"(\d{2}-\d{2}-\d{4})",
        ]
        for pat in date_patterns:
            m = re.search(pat, text)
            if m:
                result["date"] = m.group(1)
                break

    return result
