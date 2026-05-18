#!/usr/bin/env bash
set -euo pipefail

mkdir -p .tessdata

if [ ! -f .tessdata/chi_sim.traineddata ]; then
  curl -L --retry 3 \
    -o .tessdata/chi_sim.traineddata \
    https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata
fi

echo "Chinese OCR model is ready: .tessdata/chi_sim.traineddata"

