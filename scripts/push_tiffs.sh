#!/bin/bash
# Push GeoTIFF files to GitHub in batches under 500MB each.
# Re-run safe: only processes uncommitted .tif files.
#
# Usage: bash scripts/push_tiffs.sh

set -e
cd "$(dirname "$0")/.."

BATCH_LIMIT=$((500 * 1024 * 1024))  # 500MB in bytes
TIFF_DIR="data/tiff"

# Remove data/tiff/ from .gitignore if present
if grep -q "^data/tiff/" .gitignore 2>/dev/null; then
    sed -i '/^data\/tiff\//d' .gitignore
    git add .gitignore
    git commit -m "允許 data/tiff/ 加入版本控制"
    git push
fi

# Collect untracked tiff files
mapfile -t FILES < <(find "$TIFF_DIR" -name "*.tif" -exec git ls-files --error-unmatch {} \; 2>&1 | grep "not in" | sed 's/.*did not match any file(s) known to git//' | grep -oP "'[^']+'" | tr -d "'" | sort)

# Fallback: if the above didn't work, use simpler method
if [ ${#FILES[@]} -eq 0 ]; then
    mapfile -t FILES < <(git ls-files --others --exclude-standard -- "$TIFF_DIR" | grep '\.tif$' | sort)
fi

if [ ${#FILES[@]} -eq 0 ]; then
    echo "No new .tif files to push."
    exit 0
fi

# Calculate total size and batch count
TOTAL_SIZE=0
declare -a SIZES
for i in "${!FILES[@]}"; do
    s=$(stat --format=%s "${FILES[$i]}" 2>/dev/null || stat -f%z "${FILES[$i]}" 2>/dev/null)
    SIZES[$i]=$s
    TOTAL_SIZE=$((TOTAL_SIZE + s))
done

# Count batches
TOTAL_BATCHES=0
tmp_size=0
for s in "${SIZES[@]}"; do
    if [ $((tmp_size + s)) -gt $BATCH_LIMIT ] && [ $tmp_size -gt 0 ]; then
        TOTAL_BATCHES=$((TOTAL_BATCHES + 1))
        tmp_size=0
    fi
    tmp_size=$((tmp_size + s))
done
[ $tmp_size -gt 0 ] && TOTAL_BATCHES=$((TOTAL_BATCHES + 1))

echo "Found ${#FILES[@]} tif files, total $(( TOTAL_SIZE / 1024 / 1024 )) MB"
echo "Batch limit: $(( BATCH_LIMIT / 1024 / 1024 )) MB"
echo "Will push in $TOTAL_BATCHES batches"
echo ""

BATCH_NUM=0
BATCH_SIZE=0
BATCH_FILES=()

push_batch() {
    if [ ${#BATCH_FILES[@]} -eq 0 ]; then
        return
    fi
    BATCH_NUM=$((BATCH_NUM + 1))
    echo "--- Batch $BATCH_NUM/$TOTAL_BATCHES: ${#BATCH_FILES[@]} files, $(( BATCH_SIZE / 1024 / 1024 )) MB ---"

    for f in "${BATCH_FILES[@]}"; do
        git add "$f"
    done

    git commit -m "新增 GeoTIFF 資料 (batch $BATCH_NUM/$TOTAL_BATCHES: ${#BATCH_FILES[@]} files)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"

    echo "  Pushing batch $BATCH_NUM..."
    if ! git push; then
        echo "  Push failed, retrying in 5s..."
        sleep 5
        git push
    fi
    echo "  Batch $BATCH_NUM done."
    echo ""

    BATCH_FILES=()
    BATCH_SIZE=0
}

for i in "${!FILES[@]}"; do
    f="${FILES[$i]}"
    s="${SIZES[$i]}"

    if [ $((BATCH_SIZE + s)) -gt $BATCH_LIMIT ] && [ $BATCH_SIZE -gt 0 ]; then
        push_batch
    fi

    BATCH_FILES+=("$f")
    BATCH_SIZE=$((BATCH_SIZE + s))
done

push_batch

echo "All $BATCH_NUM batches pushed successfully."
