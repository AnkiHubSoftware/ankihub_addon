find . -name __pycache__ -or -regex ".*.py[cod]" -or -name .DS_Store | xargs rm -rf
python3 scripts/generate_manifest.py
python3 scripts/build.py
zip -r "../ankihub.ankiaddon" . -x ./tests\*
