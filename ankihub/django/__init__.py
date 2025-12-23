from collections import OrderedDict

from django.apps import apps
from django.conf import settings
from django.template import engines

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


def render_template(name: str) -> str:
    template = engine.get_template(name)
    text = template.render()

    return text
