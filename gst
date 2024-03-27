#!/bin/bash

current_dir="$(dirname "${BASH_SOURCE:-$0}")"
python "$current_dir/gitstack.py" "$@"
