Release History
===============

1.0.9 (unreleased)
------------------

- Nothing changed yet.


1.0.8 (2020-06-25)
------------------

-   Fixes find method returning the wrong file when more than one file
    exists with that name.
-   Handle new shared file URLS.

1.0.7 (2020-03-25)
------------------

-   Fix login by calculating public RSA exponent instead of hardcoding.

1.0.6 (2020-02-03)
------------------

-   Fixes RSA public exponent issue.
-   Switches dependency pycrypto to pycryptodome.

1.0.5 (2019-11-18)
------------------

-   Increase the wait time in between failed API request retries.

1.0.4 (2019-11-18)
------------------

-   Increase the wait time in between failed API request retries.

1.0.3 (2019-11-12)
------------------

-   Fixes broken `download` method.
-   Changes `download` and `download_url` methods to return the path to
    the downloaded file, previously returned `None`.
-   Added LICENSE.

1.0.2 (2019-11-07)
------------------

-   Reverts, "Replace pycrypto dependency with pycryptodome" as breaks
    login process.

1.0.1 (2019-11-06)
------------------

-   When a request fails due to EAGAIN response, retry with exp backoff
    up to 20 seconds.
-   Adds logging, removes print statements.
-   Replace pycrypto dependency with pycryptodome.
-   Removes Python 2 specific code.

1.0.0 (2019-10-31)
------------------

-   Removes broken method `get_contacts()`.
-   Adds support for login with a v2 Mega user account.
-   Adds `export()` method to share a file or folder, returning public
    share URL with key.
-   Adds code, message attrs to RequestError exception, makes message in
    raised exceptions include more details.
-   Alters `create_folder()` to accept a path including multiple sub
    directories, adds support to create them all (similar to 'mkdir -p'
    on unix systems).
-   Adds `exclude_deleted=True` optional arg to `find()` method, to
    exclude deleted nodes from results.

0.9.20 (2019-10-17)
-------------------

-   Python 3 bugfix to `upload` method.

0.9.19 (2019-10-16)
-------------------

-   Python 3 support and bugfixes.
-   Update packaging code.
-   Added changelog.

0.9.18 (2013-07-04)
-------------------

-   Unknown

