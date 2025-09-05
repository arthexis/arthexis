import argparse
from django.core.management import get_commands, load_command_class
from django.contrib.admindocs.views import BaseAdminDocsView


class CommandsView(BaseAdminDocsView):
    template_name = "admin_doc/commands.html"

    def get_context_data(self, **kwargs):
        commands = []
        for name, app_name in sorted(get_commands().items()):
            try:
                cmd = load_command_class(app_name, name)
                parser = cmd.create_parser("manage.py", name)
            except Exception:  # pragma: no cover - command import issues
                continue
            args = []
            options = []
            for action in parser._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if action.option_strings:
                    options.append(
                        {
                            "opts": ", ".join(action.option_strings),
                            "help": action.help or "",
                        }
                    )
                else:
                    args.append(
                        {
                            "name": action.metavar or action.dest,
                            "help": action.help or "",
                        }
                    )
            commands.append(
                {
                    "name": name,
                    "help": getattr(cmd, "help", ""),
                    "args": args,
                    "options": options,
                }
            )
        return super().get_context_data(**{**kwargs, "commands": commands})
