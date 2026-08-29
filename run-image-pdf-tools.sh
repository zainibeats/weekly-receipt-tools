#!/bin/sh
set -u

SCRIPT_SOURCE_DIR=$(dirname "$0")
# Treat script paths that start with "-" as relative paths, not command options.
case "$SCRIPT_SOURCE_DIR" in
    -*) SCRIPT_SOURCE_DIR=./$SCRIPT_SOURCE_DIR ;;
esac

# Clear CDPATH so cd prints nothing and resolves the script directory predictably.
SCRIPT_DIR=$(CDPATH= cd "$SCRIPT_SOURCE_DIR" 2>/dev/null && pwd -P)
if [ -z "$SCRIPT_DIR" ]; then
    echo "ERROR: Could not determine the script directory."
    exit 1
fi

cd "$SCRIPT_DIR" || exit 1

echo "Image PDF Tools"
echo

for required_file in scripts/make-image-grid.py scripts/append-image-page.py scripts/_common.py requirements.txt; do
    if [ ! -f "$required_file" ]; then
        echo "ERROR: $required_file was not found."
        echo "Put this file in the same folder as the project scripts."
        exit 1
    fi
done

VENV_PY=".venv/bin/python"

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)' >/dev/null 2>&1; then
                PYTHON_CMD=$candidate
                return 0
            fi
        fi
    done

    echo "ERROR: Python 3.12 or 3.13 was not found."
    echo "Install Python from https://www.python.org/downloads/"
    return 1
}

ensure_venv() {
    if [ -x "$VENV_PY" ]; then
        return 0
    fi

    if ! find_python; then
        return 1
    fi

    echo "Setting up the local Python environment. This may take a minute."
    if ! "$PYTHON_CMD" -m venv .venv; then
        echo
        echo "ERROR: Could not create the Python environment."
        echo "Install Python 3.12 or 3.13 from https://www.python.org/downloads/"
        return 1
    fi
}

ensure_dependencies() {
    if "$VENV_PY" -c 'import PIL, pillow_heif, pypdf' >/dev/null 2>&1; then
        return 0
    fi

    echo "Installing required Python packages. This may take a minute."
    if ! "$VENV_PY" -m pip install -r requirements.txt; then
        echo
        echo "ERROR: Could not install required Python packages."
        echo "Check your internet connection, then run this file again."
        return 1
    fi
}

pause() {
    printf '\nPress Enter to continue... '
    IFS= read -r unused_input || true
}

clean_path() {
    path_input=$1

    # Terminal drag-and-drop commonly wraps paths in quotes; remove one wrapper.
    case "$path_input" in
        \"*\")
            path_input=${path_input#\"}
            path_input=${path_input%\"}
            ;;
        \'*\')
            path_input=${path_input#\'}
            path_input=${path_input%\'}
            ;;
    esac

    printf '%s\n' "$path_input"
}

prompt_folder() {
    echo
    echo "Enter the image folder path."
    echo "Tip: You can drag the folder into this window, then press Enter."
    printf 'Folder: '
    IFS= read -r IMAGE_FOLDER || return 1
    IMAGE_FOLDER=$(clean_path "$IMAGE_FOLDER")

    if [ -z "$IMAGE_FOLDER" ]; then
        return 1
    fi

    if [ ! -d "$IMAGE_FOLDER" ]; then
        echo
        echo "ERROR: Folder not found:"
        echo "$IMAGE_FOLDER"
        pause
        return 1
    fi

    return 0
}

prompt_image() {
    echo
    echo "Enter the JPG/JPEG image path."
    echo "Tip: You can drag the image into this window, then press Enter."
    printf 'Image: '
    IFS= read -r IMAGE_FILE || return 1
    IMAGE_FILE=$(clean_path "$IMAGE_FILE")

    if [ -z "$IMAGE_FILE" ]; then
        return 1
    fi

    if [ ! -f "$IMAGE_FILE" ]; then
        echo
        echo "ERROR: Image file not found:"
        echo "$IMAGE_FILE"
        pause
        return 1
    fi

    return 0
}

prompt_pdf() {
    echo
    echo "Enter the PDF path."
    echo "Tip: You can drag the PDF into this window, then press Enter."
    printf 'PDF: '
    IFS= read -r PDF_FILE || return 1
    PDF_FILE=$(clean_path "$PDF_FILE")

    if [ -z "$PDF_FILE" ]; then
        return 1
    fi

    if [ ! -f "$PDF_FILE" ]; then
        echo
        echo "ERROR: PDF file not found:"
        echo "$PDF_FILE"
        pause
        return 1
    fi

    return 0
}

after_action() {
    status=$1
    echo
    if [ "$status" -eq 0 ]; then
        echo "Done."
    else
        echo "The command did not complete successfully."
    fi
    pause
}

make_grid() {
    prompt_folder || return 0

    echo
    echo "Creating image-grid.jpg in:"
    echo "$IMAGE_FOLDER"
    echo
    "$VENV_PY" scripts/make-image-grid.py "$IMAGE_FOLDER"
    after_action "$?"
}

append_image() {
    prompt_image || return 0
    prompt_pdf || return 0

    echo
    echo "Appending image to PDF."
    echo
    "$VENV_PY" scripts/append-image-page.py "$IMAGE_FILE" --pdf "$PDF_FILE"
    after_action "$?"
}

full_workflow() {
    prompt_folder || return 0
    prompt_pdf || return 0

    echo
    echo "Step 1 of 2: Creating image-grid.jpg in:"
    echo "$IMAGE_FOLDER"
    echo
    "$VENV_PY" scripts/make-image-grid.py "$IMAGE_FOLDER"
    status=$?
    if [ "$status" -ne 0 ]; then
        after_action "$status"
        return 0
    fi

    GRID_FILE=$IMAGE_FOLDER/image-grid.jpg

    echo
    echo "Step 2 of 2: Appending image-grid.jpg to the PDF."
    echo
    "$VENV_PY" scripts/append-image-page.py "$GRID_FILE" --pdf "$PDF_FILE"
    after_action "$?"
}

if ! ensure_venv || ! ensure_dependencies; then
    exit 1
fi

while :; do
    if [ -t 1 ] && command -v clear >/dev/null 2>&1; then
        clear
    fi

    echo "Image PDF Tools"
    echo
    echo "1. Make an image grid from a folder"
    echo "2. Append a JPG image page to a PDF"
    echo "3. Make an image grid, then append it to a PDF"
    echo "4. Exit"
    echo
    printf 'Choose 1, 2, 3, or 4: '
    IFS= read -r CHOICE || exit 0

    case "$CHOICE" in
        1) make_grid ;;
        2) append_image ;;
        3) full_workflow ;;
        4) exit 0 ;;
        *)
            echo
            echo "Please choose 1, 2, 3, or 4."
            pause
            ;;
    esac
done
