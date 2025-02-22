#!/bin/bash

set -e

# use calver and increment version number if necessary
version="$(date +%Y-%m-%d).1"
versionSplit=(${version//./ })

prevTag=`git tag --sort=-creatordate | grep ${versionSplit[0]} | head -n 1`
prevTagSplit=(${prevTag//./ })

if [ "${prevTagSplit[0]}" = "${versionSplit[0]}" ]; then
    version=${prevTagSplit[0]}.$((prevTagSplit[1] + 1))
fi

echo $version
