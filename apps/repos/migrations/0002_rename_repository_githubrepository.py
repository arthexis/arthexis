from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("repos", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Repository",
            new_name="GitHubRepository",
        ),
        migrations.AlterModelOptions(
            name="githubrepository",
            options={
                "verbose_name": "GitHub Repository",
                "verbose_name_plural": "GitHub Repositories",
            },
        ),
        migrations.RemoveConstraint(
            model_name="githubrepository",
            name="unique_repository_owner_name",
        ),
        migrations.AddConstraint(
            model_name="githubrepository",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_github_repository_owner_name",
            ),
        ),
    ]
