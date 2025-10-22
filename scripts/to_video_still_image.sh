#!/bin/bash

show_help() {
    echo "Usage: $0 [OPTIONS] <audiofile> [imagefile]"
    echo ""
    echo "Convert an audio file to a video with a still image."
    echo ""
    echo "Arguments:"
    echo "  audiofile    Path to the input audio file (required)"
    echo "  imagefile    Path to the image file (optional, defaults to cover.jpg in script directory)"
    echo ""
    echo "Options:"
    echo "  -h           Show this help message and exit"
    echo "  -o OUTPUT    Output video file (defaults to audiofile with .mp4 extension)"
    echo ""
    echo "Output:"
    echo "  Creates video file in the current directory"
}

# Initialize variables
output_file=""

# Parse options
while getopts "ho:" opt; do
    case $opt in
        h)
            show_help
            exit 0
            ;;
        o)
            output_file="$OPTARG"
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            echo "Use -h for help"
            exit 1
            ;;
    esac
done

# Shift past the options
shift $((OPTIND-1))

audiofile="$1"

if [ -z "$audiofile" ]; then
  echo "Usage: $0 <audiofile>"
  echo "Use -h for help"
  exit 1
fi
if [ ! -f "$audiofile" ]; then
  echo "Audio file '$audiofile' not found!"
  exit 1
fi

# Set default output file if not specified
if [ -z "$output_file" ]; then
    output_file="${audiofile%.*}.mp4"
fi

imagefile="$2"

if [ -z "$imagefile" ]; then
    dirname=$(dirname  "$0")
    imagefile="$dirname/cover.jpg"
fi

if [ ! -f "$imagefile" ]; then
    echo "Image file '$imagefile' not found!"
    echo "Use -h for help"

    exit 1
fi

ffmpeg -framerate 1 -loop 1 -i "$imagefile" -i "$audiofile" -c:v libx264 -tune stillimage -c:a copy -shortest -pix_fmt yuv420p "$output_file"