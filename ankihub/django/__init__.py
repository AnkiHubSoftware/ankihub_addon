from collections import OrderedDict
from typing import Any, Dict, Optional

from django.apps import apps
from django.conf import settings
from django.template import engines
from django_cotton.cotton_loader import CottonCompiler

from ..settings import ADDON_PATH

settings.configure()

settings.DATABASES = {}
settings.ADMIN_ENABLED = False
settings.INSTALLED_APPS = ("django_cotton", f"{ADDON_PATH.name}.django.app")
settings.TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["templates"],
        "OPTIONS": {
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django_cotton.cotton_loader.Loader",
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
            "context_processors": [],
            "builtins": [
                "django_cotton.templatetags.cotton",
            ],
        },
    }
]
apps.app_configs = OrderedDict()
apps.ready = False
apps.populate(settings.INSTALLED_APPS)
engine = engines["django"]
cotton_compiler = CottonCompiler()


def render_template(name: str, context: Optional[Dict[str, Any]] = None) -> str:
    template = engine.get_template(name)
    text = template.render(context=context)

    return text


def render_template_from_string(html: str, context: Optional[Dict[str, Any]] = None) -> str:
    html = cotton_compiler.process(html)
    template = engine.from_string(html)
    text = template.render(context=context)

    return text
