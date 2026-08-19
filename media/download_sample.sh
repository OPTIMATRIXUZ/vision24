#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

URL="${1:-https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi}"
RAW="raw_download"

echo "Downloading $URL ..."
curl -L --fail -o "$RAW" "$URL"

echo "Normalizing to sample.mp4 ..."
ffmpeg -y -i "$RAW" -an -c:v libx264 -pix_fmt yuv420p -r 25 -preset veryfast sample.mp4

rm -f "$RAW"
echo "Done: media/sample.mp4"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 sample.mp4
