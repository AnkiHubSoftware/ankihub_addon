find . -regex '^.*\(__pycache__\|\.py[co]\)$' -delete
zip -r "../ankihub.ankiaddon" . -x ./tests\*
