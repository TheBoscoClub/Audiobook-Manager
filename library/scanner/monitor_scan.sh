#!/bin/bash
# Audiobook Scan Progress Monitor

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load configuration
source "$SCRIPT_DIR/../../lib/audiobook-config.sh"

LOG_FILE="/tmp/audiobook-scan.log"
TOTAL_FILES=3985

# Check if scan is running
if ! pgrep -f "scan_audiobooks.py" > /dev/null; then
    echo "❌ Scan is not running!"
    echo ""
    if [ -f "$LOG_FILE" ]; then
        echo "Last scan completed. Check results below."
    else
        echo "No scan has been started yet."
        exit 1
    fi
fi

# Clear screen for better display (optional - comment out if you don't want this)
clear

echo "========================================"
echo "   Audiobook Library Scan Monitor"
echo "========================================"
echo ""

# Get current progress
if [ -f "$LOG_FILE" ]; then
    PROCESSED=$(grep -c "Processing" "$LOG_FILE")
    ERRORS=$(grep -c "Error reading" "$LOG_FILE")
    SUCCESSFUL=$((PROCESSED - ERRORS))

    # Calculate percentage
    PERCENT=$(awk "BEGIN {printf \"%.1f\", ($PROCESSED / $TOTAL_FILES) * 100}")

    # Calculate estimated time remaining (rough estimate)
    if [ "$PROCESSED" -gt 0 ]; then
        # Get time since scan started (use log file creation time)
        START_TIME=$(stat -c %Y "$LOG_FILE" 2>/dev/null || stat -f %B "$LOG_FILE" 2>/dev/null)
        CURRENT_TIME=$(date +%s)
        ELAPSED_SEC=$((CURRENT_TIME - START_TIME))
        ELAPSED_MIN=$((ELAPSED_SEC / 60))

        # Calculate rate and estimate
        if [ $ELAPSED_SEC -gt 0 ]; then
            RATE=$(awk "BEGIN {printf \"%.2f\", $PROCESSED / $ELAPSED_SEC}")
            REMAINING=$((TOTAL_FILES - PROCESSED))
            ETA_SEC=$(awk "BEGIN {printf \"%.0f\", $REMAINING / $RATE}")
            ETA_MIN=$((ETA_SEC / 60))
            ETA_HOURS=$((ETA_MIN / 60))
            ETA_MIN_REMAIN=$((ETA_MIN % 60))
        fi
    fi

    # Display progress
    echo "📊 PROGRESS"
    echo "─────────────────────────────────────"
    echo "  Files Processed: $PROCESSED / $TOTAL_FILES"
    echo "  Completion:      $PERCENT%"
    echo ""

    # Progress bar
    BAR_WIDTH=40
    FILLED=$(awk "BEGIN {printf \"%.0f\", ($PERCENT / 100) * $BAR_WIDTH}")
    printf "  ["
    for ((i=0; i<BAR_WIDTH; i++)); do
        if [ "$i" -lt "$FILLED" ]; then
            printf "█"
        else
            printf "░"
        fi
    done
    printf "] $PERCENT%%\n"
    echo ""

    # Results
    echo "📈 RESULTS"
    echo "─────────────────────────────────────"
    echo "  ✓ Successful:    $SUCCESSFUL"
    echo "  ✗ Errors/Empty:  $ERRORS"
    echo ""

    # Time estimates
    if [ -n "$ELAPSED_MIN" ]; then
        echo "⏱️  TIME"
        echo "─────────────────────────────────────"
        echo "  Elapsed:     ${ELAPSED_MIN}m"
        if [ -n "$ETA_HOURS" ] && [ "$ETA_HOURS" -gt 0 ]; then
            echo "  Remaining:   ~${ETA_HOURS}h ${ETA_MIN_REMAIN}m"
        elif [ -n "$ETA_MIN" ]; then
            echo "  Remaining:   ~${ETA_MIN}m"
        fi
        if [ -n "$RATE" ]; then
            echo "  Rate:        $(printf "%.1f" "$RATE") files/sec"
        fi
        echo ""
    fi

    # Recent files
    echo "📚 RECENT FILES"
    echo "─────────────────────────────────────"
    tail -5 "$LOG_FILE" | grep "Processing" | sed 's/Processing [0-9]*\/[0-9]*: /  • /'
    echo ""

    # Status
    echo "🔄 STATUS"
    echo "─────────────────────────────────────"
    if pgrep -f "scan_audiobooks.py" > /dev/null; then
        PID=$(pgrep -f "scan_audiobooks.py")
        echo "  ✓ Scan is running (PID: $PID)"
    else
        echo "  ✓ Scan completed!"
    fi

    # Check if scan is complete
    if [ "$PROCESSED" -ge "$TOTAL_FILES" ]; then
        echo ""
        echo "🎉 SCAN COMPLETE! 🎉"
        echo ""
        echo "Output file: $AUDIOBOOKS_HOME/library/data/audiobooks.json"
        echo ""
        echo "Next steps:"
        echo "  1. Launch the library: cd $AUDIOBOOKS_HOME && ./launch.sh"
        echo "  2. Or click 'Refresh' in the web interface if already open"
    fi

else
    echo "⚠️  Log file not found: $LOG_FILE"
fi

echo ""
echo "========================================"
echo "Log file: $LOG_FILE"
echo ""
echo "Commands:"
echo "  • Watch live:  tail -f $LOG_FILE"
echo "  • Re-run this: $0"
echo "  • Auto-update: watch -n 5 $0"
echo "========================================"
