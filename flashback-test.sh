#!/bin/bash

conda deactivate

test -f .venv/bin/activate
if [ $? -ne 0 ]; then
    echo "Virtual env not found at .venv"
    echo "Create one with uv venv"
    exit 1
fi

source .venv/bin/activate
uv pip install --reinstall .[dev,embedding,screenshot,search]

# remove default data storage
rm -rf /home/jamesbrown/.local/share/flashback-terminal
flashback-terminal $@