python3 scripts/build.py
find . -name __pycache__ -or -regex ".*.py[cod]" -or -name .DS_Store | xargs rm -rf
zip -r "../ankihub.ankiaddon" . -x ./tests\*
