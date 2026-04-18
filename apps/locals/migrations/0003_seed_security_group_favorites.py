from django.conf import settings
from django.db import migrations
from django.db.models import Max

PRODUCT_DEVELOPER_GROUP_NAME = "Product Developer"
SITE_OPERATOR_GROUP_NAME = "Site Operator"

PRODUCT_DEVELOPER_TARGETS = (
    ("app", "application"),
    ("release", "packagerelease"),
    ("repos", "repositoryissue"),
    ("tests", "suitetest"),
)
SITE_OPERATOR_TARGETS = (
    ("cards", "rfidattempt"),
    ("django_celery_beat", "periodictask"),
)


def _favorite_targets_for_user(user, group_name_map: dict[int, set[str]]) -> tuple[tuple[str, str], ...]:
    group_names = group_name_map.get(user.pk, set())
    targets: list[tuple[str, str]] = []
    if PRODUCT_DEVELOPER_GROUP_NAME in group_names:
        targets.extend(PRODUCT_DEVELOPER_TARGETS)
    if SITE_OPERATOR_GROUP_NAME in group_names:
        targets.extend(SITE_OPERATOR_TARGETS)
    return tuple(dict.fromkeys(targets))


def seed_security_group_favorites(apps, schema_editor):
    Favorite = apps.get_model("locals", "Favorite")
    ContentType = apps.get_model("contenttypes", "ContentType")
    SecurityGroup = apps.get_model("groups", "SecurityGroup")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    group_rows = SecurityGroup.objects.filter(
        name__in=(PRODUCT_DEVELOPER_GROUP_NAME, SITE_OPERATOR_GROUP_NAME)
    ).prefetch_related("user_set")

    group_name_map: dict[int, set[str]] = {}
    for group in group_rows:
        for user_id in group.user_set.values_list("id", flat=True):
            group_name_map.setdefault(user_id, set()).add(group.name)

    if not group_name_map:
        return

    content_type_by_target: dict[tuple[str, str], int] = {}
    for app_label, model in set(PRODUCT_DEVELOPER_TARGETS + SITE_OPERATOR_TARGETS):
        content_type_id = (
            ContentType.objects.filter(app_label=app_label, model=model)
            .values_list("id", flat=True)
            .first()
        )
        if content_type_id:
            content_type_by_target[(app_label, model)] = content_type_id

    user_ids = list(group_name_map.keys())
    users = User.objects.filter(id__in=user_ids)
    all_target_content_type_ids = set(content_type_by_target.values())
    existing_by_user: dict[int, set[int]] = {}
    for user_id, content_type_id in Favorite.objects.filter(
        user_id__in=user_ids,
        content_type_id__in=all_target_content_type_ids,
    ).values_list("user_id", "content_type_id"):
        existing_by_user.setdefault(user_id, set()).add(content_type_id)

    priority_by_user = {
        row["user_id"]: row["max_priority"]
        for row in Favorite.objects.filter(user_id__in=user_ids)
        .values("user_id")
        .annotate(max_priority=Max("priority"))
    }

    for user in users:
        user_targets = _favorite_targets_for_user(user, group_name_map)
        content_type_ids = [
            content_type_by_target[target]
            for target in user_targets
            if target in content_type_by_target
        ]
        if not content_type_ids:
            continue

        existing = existing_by_user.get(user.pk, set())
        max_priority = priority_by_user.get(user.pk)
        next_priority = (max_priority or -1) + 1

        new_favorites = []
        for content_type_id in content_type_ids:
            if content_type_id in existing:
                continue
            new_favorites.append(
                Favorite(
                    user_id=user.pk,
                    content_type_id=content_type_id,
                    priority=next_priority,
                    user_data=True,
                    is_user_data=True,
                )
            )
            next_priority += 1

        if new_favorites:
            Favorite.objects.bulk_create(new_favorites)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0001_initial"),
        ("cards", "0005_alter_offeringsoul_options"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("django_celery_beat", "0020_googlecalendarprofile"),
        ("groups", "0003_seed_ap_user_group"),
        ("locals", "0002_initial"),
        ("release", "0002_alter_package_test_command"),
        ("repos", "0003_githubresponsetemplate"),
        ("tests", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(seed_security_group_favorites, migrations.RunPython.noop),
    ]
