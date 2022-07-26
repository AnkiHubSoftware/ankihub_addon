set -e

python3 scripts/build.py

cd ankihub
find . -name __pycache__ -or -regex ".*.py[cod]" -or -name .DS_Store -or -name ".pytest_cache" | xargs rm -rf
zip -r "../ankihub.ankiaddon" . -x ./tests\*
