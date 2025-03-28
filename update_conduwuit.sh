#!/bin/bash

# Navigate to the repository directory
cd "$HOME/workshop/conduwuit" || exit

# Pull the latest changes
git pull

# Build the Docker image using Nix
nix build -L --extra-experimental-features "nix-command flakes" .#oci-image-x86_64-linux-musl-all-features

# Use the result symlink to find the image tarball
IMAGE_TAR_PATH=$(readlink -f result)

# Load the image into Docker and tag it
docker load < "$IMAGE_TAR_PATH" | awk '/Loaded image:/ { print $3 }' | xargs -I {} docker tag {} conduwuit:custom

# Confirm tagging
echo "Docker image tagged as conduwuit:custom"
