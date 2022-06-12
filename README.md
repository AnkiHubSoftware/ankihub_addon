# AnkiHub Addon

## Development

### Creating a development environment

To create a development environment, create a python virtual environment and
install the dependencies:

```
pip install -r ./requirements/dev.txt
```

## Environment variables

- `API_URL_BASE=https://ankihub.applikuapp.com/api`

This is needed to point the add-on at our staging instance.

- `SKIP_INIT=1`

See ./ankihub_addon/ankihub/__init__.py for what this does.  You need to set this when running tests.

Defining a `.envrc` with [`direnv`](https://direnv.net/) is great for managing env vars.

### Development workflow

#### Symlinking the add-on source

To see your changes to this repo reflected in Anki, you can symlink this repo to
the Anki add-on directory. To find the path to Anki's add-on directory, [see
this section of the
documentation](https://addon-docs.ankiweb.net/addon-folders.html#add-on-folders).

> You can access the top level add-ons folder by going to the Tools>Add-ons menu
> item in the main Anki window. Click on the View Files button, and a folder
> will pop up. If you had no add-ons installed, the top level add-ons folder
> will be shown. If you had an add-on selected, the add-on’s module folder will
> be shown, and you will need to go up one level.

For example:

```
ln -s /Users/username/anki/ankihub_addon/ankihub
/Users/username/Library/Application\ Support/Anki2/addons21/
```

You will need to restart Anki in order for Anki to reload the add-on source
code.

#### A better development experience

For a much better experience, follow the instructions below for automatically
restarting Anki when add-on source files are modified, and to see both add-on
and Anki source-code output in the console.

1. Clone Anki: `git clone https://github.com/ankitects/anki`
2. Follow the instructions [here](https://github.com/ankitects/anki/blob/main/docs/development.md) to build Anki from source.
3. Install [watchexec](https://github.com/watchexec/watchexec), e.g.

    ```
    brew install watchexec
    ```

4. From the root of the *anki* repo (not the add-on repo) you cloned in step one, run watchexec. E.g.,
   `watchexec -r -e py -w ~/Projects/anki_addons/ankihub_addon -- ./run`


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
there is currently a limitation on macos that only allows for running tests one
at a time. The current recommended workflow is to run a single test locally,
corresponding to whatever feature you are working on, and push your changes to
GitHub frequently in order to see output for the entire test suite from the
GitHub Actions workflow. Of course, if you are developing on Linux this should
not be a problem. Here is an example to run a specific test: `pytest
test_register_decks.py::test_note_type_preparations`. Refer [this
section](https://docs.pytest.org/en/6.2.x/usage.html#specifying-tests-selecting-tests)
of pytest's docs for details.
