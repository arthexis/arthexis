from post_office.apps import PostOfficeConfig as BasePostOfficeConfig


class WorkgroupConfig(BasePostOfficeConfig):
    """Customize Post Office app label."""

    verbose_name = "6. Workgroup"
