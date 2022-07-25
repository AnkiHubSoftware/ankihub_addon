It may not be possible to delete the add-on during runtime.
In that case, you will need to open the addon directory by clicking 'View Files',
close Anki, and manually delete the addon directory.

# Development

Run the following code to get a list of packages to add to requirements.txt

```bash
pip install --target=./temp/libs <package_name>
pip freeze --path=./temp/libs > temp_reqs.txt
```

Manually edit the temp_reqs.txt to remove dependencies already bundled with Anki,
then run the following code to install those packages

```bash
pip install --no-deps --target=./libs -r requirements.txt
```
