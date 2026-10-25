#!/bin/bash

cd "$(dirname "$BASH_SOURCE")"
mkdir -p build
cd build
rm CMakeCache.txt
cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc 2>/dev/null || echo "1")
