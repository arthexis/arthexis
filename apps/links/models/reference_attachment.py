"""Generic reference attachment model."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q


class ReferenceAttachment(models.Model):
    """Attach a reference record to any model instance."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.TextField()
    content_object = GenericForeignKey("content_type", "object_id")
    reference = models.ForeignKey(
        "links.Reference",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    slot = models.CharField(max_length=32, blank=True, default="")
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sort_order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "reference"],
                name="links_refattachment_ct_obj_ref_uniq",
            ),
            models.UniqueConstraint(
                fields=["content_type", "object_id", "slot"],
                condition=Q(is_primary=True),
                name="links_refattachment_ct_obj_slot_primary_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference_id}:{self.content_type_id}:{self.object_id}"
