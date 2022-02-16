# AnkiHub Addon

## Development

To create a development environment, create a python virtual environment and
install the dependencies:

```
pip install -r ./requirements/dev.txt
```

## Tests and static checks

The entire test suite and static code checks will be run automatically with
GitHub actions when you open a PR on GitHub . See `/.github/workflows/ci.yml`
for details.

### Running tests locally

The test suite relies on the `pytest-anki` plugin for pytest. Unfortunately,
there is currently a limitation on macos that only allows for running tests one
at a time. The current recommended workflow is to run a single test locally,
corresponding to whatever feature you are working on, and push your changes to
GitHub frequently in order to see output for the entire test suite from the
GitHub Actions workflow. Of course, if you are developing on Linux this should
not be a problem. Here is an example to run a specific test: `pytest
test_register_decks.py::test_note_type_preparations`. Refer [this
section](https://docs.pytest.org/en/6.2.x/usage.html#specifying-tests-selecting-tests)
of pytest's docs for details.
