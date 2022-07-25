python3 scripts/update_media_import.py
find . -regex '^.*\(__pycache__\|\.py[co]\)$' -delete
zip -r "../ankihub.ankiaddon" . -x ./tests\*
