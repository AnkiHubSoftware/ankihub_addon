# AnkiHub Addon

## Development

### Devcontainer
This repo has a devcontainer which can be used:
- locally in VSCode (Run `Dev Containers: Open folder in Container...`, choose the repo folder)
- remotely in GitHub CodeSpaces

After opening the devcontainer you can open `localhost:6080` in a browser to see the desktop environment of the container.

### Requirements for creating a development environment (without the devcontainer).
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
- Set `GOOGLE_API_KEY`
  - Get this value from the `.envrc` in BitWarden (ask if you don't have permission)
- Set `ANKIHUB_APP_URL`
  - This environment variable overrides `ankihub_url` in the add-on config.
- Set `S3_BUCKET_URL`
- Set `REPORT_ERRORS` from 0 to 1 if you want to capture Sentry errors

#### Run the build script
`python scripts/build.py`

You only have to do this once.

#### Set up VSCode workspace with Anki source code (optional)
During development of the add-on it is convenient to be able to navigate and search in Anki's source code in addition to the add-on source code.
This can be achieved using VSCode workspaces (https://code.visualstudio.com/docs/editor/workspaces).
- Copy `ankihub.code-workspace.dev` to `ankihub.code-workspace`:  `cp ankihub.code-workspace.dev ankihub.code-workspace`
- Replace the paths in `ankihub.code-workspace` as described by the comments in the file.

### Development workflow
There are two VSCode debug launch configurations (defined in `launch.json`).
They both set up Anki's base directory on a path you can configure using an environment variable.
They also create a symlink from the add-on directory to the add-on source - this way you can make changes to the
add-on code, restart the debug session and Anki will use the updated add-on code.

- Run Anki with ANKI_BASE.

  This launch configuration sets up an Anki base directory in `ANKI_BASE` if it doesn't exist yet and re-uses it otherwise.
  This means that the AnkiHub add-on configuration and Anki's decks, notes, settings etc. will be retained between launches.


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

#### Running performance tests
##### All addon tests including performance tests
```
pytest tests/addon --performance
```

##### Only performance tests
```
pytest tests/addon --peformance -k performance
```

#### Type checking
```
mypy
```
