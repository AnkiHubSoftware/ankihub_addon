# AnkiHub Addon

## Development

### Requirements for creating a development environment.
#### Set up a virtual environment and VSCode

- Create a python virtual environment in your preferred manner.
- Install the dependencies into that environment: `pip install -r ./requirements/dev.txt`
- Open VSCode in this repo:  `code .`
- Open the command palette in VSCode, type `Python: Select interpreter`, and set the Python interpreter to the one in virtual environment you created.
- Install [`direnv`](https://direnv.net/docs/installation.html).
- Install the `direnv` extension for VSCode: `code --install-extension Rubymaniac.vscode-direnv` (or from VSCode)

#### Configure environment variables

- Copy `.envrc.dev` to `.envrc`:  `cp .envrc.dev .envrc`
- Modify the newly created `.envrc`:
  - Set `ANKI_EXEC` to the path of your `anki` executable.
    You can find this by activating your virtual environment and typing `which anki`.
- Set `GOOGLE_API_KEY`
  - Get this value from the `.envrc` in BitWarden (ask if you don't have permission)
- Change `ANKIHUB_APP_URL` from http://localhost:8000 to https://staging.ankihub.net/, for example, to point the add-on at a different AnkiHub instance.
  - This environment variable overrides `ankihub_url` in the add-on config.
- You can change `REPORT_ERRORS` from 0 to 1 if you want to capture Sentry errors.
- `SKIP_INIT=1` (you don't need to add or change this)
  - See `./ankihub_addon/ankihub/__init__.py` for what this does.  You need to set this when running tests.

#### Run the build script
`python scripts/build.py`

You only have to do this once.

### Development workflow
There are two VsCode debug launch configurations (defined in `launch.json`).
They both set up Anki's base directory on a path you can configure using an environment variable.
They also create a symlink from the add-on directory to the add-on source - this way you can make changes to the 
add-on code, restart the debug session and Anki will use the updated add-on code.

- Run Anki with TEMPORARY_ANKI_BASE.

  This launch configuration sets up a clean Anki base directory in `TEMPORARY_ANKI_BASE` every time it starts.

- Run Anki with ANKI_BASE.

  This launch configuration sets up an Anki base directory in `ANKI_BASE` if it doesn't exist yet and re-uses it otherwise.
  This means that the AnkiHub add-on configuration and Anki's decks, notes, settings etc. will be retained between launches.


### Alternative development workflow with automatic reloading when source code is modified (without debugging)

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
