#!/bin/bash

echo "🧹 LiveKit Worker Cleanup Script"
echo "================================"

# Function to count processes
count_processes() {
    ps aux | grep -E "$1" | grep -v grep | wc -l
}

# Show current state
echo "📊 Before cleanup:"
echo "  - Worker processes: $(count_processes 'agent.py')"
echo "  - Spawn processes: $(count_processes 'multiprocessing.spawn')"
echo "  - Resource trackers: $(count_processes 'resource_tracker')"
echo ""

# Kill main worker processes
echo "🔪 Killing worker processes..."
pkill -9 -f "agent.py" 2>/dev/null

# Kill multiprocessing spawn processes
echo "🔪 Killing spawn processes..."
pkill -9 -f "multiprocessing.spawn" 2>/dev/null

# Kill resource trackers
echo "🔪 Killing resource trackers..."
pkill -9 -f "multiprocessing.resource_tracker" 2>/dev/null

# Kill any suspended Python processes in current directory
echo "🔪 Killing suspended Python processes..."
ps aux | grep python | grep " T " | awk '{print $2}' | xargs -r kill -9 2>/dev/null

# Wait a moment
sleep 1

# Verify cleanup
echo ""
echo "✅ After cleanup:"
echo "  - Worker processes: $(count_processes 'agent.py')"
echo "  - Spawn processes: $(count_processes 'multiprocessing.spawn')"
echo "  - Resource trackers: $(count_processes 'resource_tracker')"

# Check for any remaining Python processes
remaining=$(ps aux | grep -E "python.*agent|multiprocessing" | grep -v grep | wc -l)
if [ $remaining -gt 0 ]; then
    echo ""
    echo "⚠️  Warning: $remaining processes still running:"
    ps aux | grep -E "python.*agent|multiprocessing" | grep -v grep
else
    echo ""
    echo "✨ All clean! Ready to start fresh."
fi