set -e

python3 scripts/build.py

mkdir -p dist
rm -rf dist/release
cp -r ankihub dist/release

cd dist/release

# update version file
../../scripts/calver.sh > VERSION

# remove temporary files
find . -name __pycache__ -or -regex ".*.py[cod]" -or -name .DS_Store -or -name ".pytest_cache" | xargs rm -rf

zip -r "../../ankihub.ankiaddon" . -x ./tests\*
