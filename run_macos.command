#!/bin/zsh

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR" || exit 1
python3 -m doki_exporter
STATUS=$?
echo
read "REPLY?Нажмите Enter, чтобы закрыть окно…"
exit $STATUS
