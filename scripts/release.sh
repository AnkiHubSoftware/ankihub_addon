python3 scripts/build.py
find . -regex '^.*\(__pycache__\|\.py[co]\)$' -delete
zip -r "../ankihub.ankiaddon" . -x ./tests\*
