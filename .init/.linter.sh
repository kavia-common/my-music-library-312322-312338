#!/bin/bash
cd /home/kavia/workspace/code-generation/my-music-library-312322-312338/music_player_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

