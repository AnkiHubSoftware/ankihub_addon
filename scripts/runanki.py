import subprocess
import sys

subprocess.run(["anki", *sys.argv[1:]])
