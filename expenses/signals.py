from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Company, ApprovalPolicy

@receiver(post_save, sender=Company)
def ensure_policy_for_company(sender, instance: Company, created: bool, **kwargs):
    if created:
        ApprovalPolicy.objects.get_or_create(company=instance)
