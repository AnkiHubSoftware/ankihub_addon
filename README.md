# AnkiHub Addon

## Development

### Creating a development environment

To create a development environment, create a python virtual environment and
install the dependencies:

```
pip install -r ./requirements/dev.txt
```

### Staging server

To point the add-on to the staging server, modify add-on config to `"ankihub_url": "https://staging.ankihub.net"`

### Environment variables

- `ANKIHUB_APP_URL=url`

Overrides `ankihub_url` add-on config.

- `SKIP_INIT=1`

See `./ankihub_addon/ankihub/__init__.py` for what this does.  You need to set this when running tests.

Defining a `.envrc` with [`direnv`](https://direnv.net/) is great for managing env vars. Here is a sample
`.envrc`:

```
export ADDONS_DIR=~/Library/Application\ Support/Anki2/addons21/
export DEVELOPMENT=True
export ANKIHUB_APP_URL=https://staging.ankihub.net
export QTWEBENGINE_REMOTE_DEBUGGING=8080
export ANKIDEV=1
export LOGTERM=1
export ANKI_BASE=tests/test_data/Anki2
export ANKI_PROFILE=dev
export LOGTERM=1
```

### Development workflow

Follow the instructions below for:
- Automatically restarting Anki when add-on source code is modified
- To see both add-on and Anki source-code output in the console
- Start Anki with the data dir specified by the `ANKI_BASE` env var and `ANKI_PROFILE`
  - This runs Anki in an environment that is completely isolated from the default data directory.

1. Install [watchexec](https://github.com/watchexec/watchexec), e.g.

    ```
    brew install watchexec
    ```

2. From the root of the this repo, run `anki` under watchexec:

```bash
ANKIHUB_APP_URL=http://localhost:8000 \
    watchexec -r -d 3000 -w ankihub -- \
    anki -p $ANKI_PROFILE
```


## Tests and static checks

The entire test suite and static code checks will be run automatically with
GitHub actions when you open a PR on GitHub . See `/.github/workflows/ci.yml`
for details.

### Running tests locally

In order to run the tests locally you will also need to install an X Server for X11 forwarding.
On macos:

```
brew install xquartz
```

The test suite relies on the `pytest-anki` plugin for pytest. Unfortunately,
running tests on macos tends to be unreliable. The current recommended workflow
is to push your changes to GitHub frequently in order to see output for the
entire test suite from the GitHub Actions workflow. Of course, if you are
developing on Linux this should not be a problem.

Alternatively, use the https://gitpod.io/ development environments that are
automatically created for pull requests. Links to Gitpod workspaces are
automatically added as a comment on PRs. You can also find a link in the checks
section of PRs.

Here is an example to run a specific test: `pytest
test_register_decks.py::test_note_type_preparations`. Refer [this
section](https://docs.pytest.org/en/6.2.x/usage.html#specifying-tests-selecting-tests)
of pytest's docs for details.

Tests are seperated into add-on tests and client tests.

#### Running add-on tests
```
pytest tests/addon
```

#### Running client tests
##### With vcr
```
pytest tests/client
```
See https://vcrpy.readthedocs.io/en/latest/.

##### Without vcr
This requires ankihub runnig locally on localhost:8000. **The test setup clears the ankihub database.**
```
pytest tests/client --disable-vcr
```

#### Type checking
```
mypy
```
