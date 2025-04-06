#!/bin/bash

# Run the canary script
python3 sw1tch/canary.py

# Check if the canary.txt was generated successfully
if [ $? -eq 0 ] && [ -f sw1tch/data/canary.txt ]; then
    # Stage all changes, commit, and push
    git add sw1tch/data/canary.txt
    git commit -m "Update warrant canary - $(date +%Y-%m-%d)"
    git push origin main
    echo "Warrant canary updated and pushed to repository."
else
    echo "Failed to generate or find canary.txt. Git operations aborted."
    exit 1
fi
