#!/bin/bash
# Sequential research queue: ./run_queue.sh SYM1 SYM2 ...
cd /home/claude/cryptonova-quant
for s in "$@"; do
  python3 ${SCRIPT:-scripts/levels_multi.py} "$s" > "data/${TAG:-levels}_$s.log" 2>&1
done
