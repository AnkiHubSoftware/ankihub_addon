

URL_BASE = "hub.ankipalace.com/"
URL_VIEW_NOTE = URL_BASE + "note/"

FIELD_NAME = "AnkiHub nid (hidden)"

TEMPLATE_LINK_TEXT = "View Note on AnkiHub"
LINK_HTML = "<a class='ankihub' href='{}'>{}</a>".format(
    URL_VIEW_NOTE + "{{%s}}" % FIELD_NAME, TEMPLATE_LINK_TEXT)
