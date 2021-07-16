import requests
import json
import io
FILE_LOCATION = "/Users/johnpincock/Library/Application Support/Anki2/addons21/ankihub/upload_apkg.apkg"

def read_apkg(apkg):
    with open(apkg, "rb") as fh:
        apkg_bytes = io.BytesIO(fh.read())
    print(apkg_bytes)
    return apkg_bytes

headers = {
    "Authorization": "Token 4b9470ca533186c7820fd9c8fdbf73803dd5c067",
}
url="http://localhost:8000/api/deck_upload/"
test_file = open(FILE_LOCATION, "rb")
file_name = "test"

response = requests.post(
    url,
    files={
        "file": test_file,
    },
    data={
        "filename": file_name
    },
    headers=headers
)

print(response)
print(response.content)
