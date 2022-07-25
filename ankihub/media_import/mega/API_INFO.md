Mega API information
=====================

This file contains definitions for some of the properties within the API. The aim of the file is that more people will contribute through understanding.


### Node attributes (json properties)

*   'a' Type
*   'h' Id
*   'p' Parent Id
*   'a' encrypted Attributes (within this: 'n' Name)
*   'k' Node Key
*   'u' User Id
*   's' Size
*   'ts' Time Stamp

#### Node types

*   0 File
*   1 Folder
*   2 Root Folder
*   3 Inbox
*   4 Trash
*   -1 Dummy


### Error responses

#### General errors:
*   EINTERNAL (-1):
*   EARGS (-2):
*   EAGAIN (-3)
*   ERATELIMIT (-4):

#### Upload errors:
*   EFAILED (-5):
*   ETOOMANY (-6):
*   ERANGE (-7):
*   EEXPIRED (-8):

#### Filesystem/Account level errors:
*   ENOENT (-9):
*   ECIRCULAR (-10):
*   EACCESS (-11):
*   EEXIST (-12):
*   EINCOMPLETE (-13):
*   EKEY (-14):
*   ESID (-15):
*   EBLOCKED (-16):
*   EOVERQUOTA (-17):
*   ETEMPUNAVAIL (-18):

