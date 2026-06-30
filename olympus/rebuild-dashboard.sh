#!/bin/bash
# CERES OS Dashboard Rebuild Script
# This script forces a complete rebuild of the dashboard container

set -e  # Exit on error

echo "========================================="
echo "CERES OS Dashboard Rebuild"
echo "========================================="
echo ""

# Stop the dashboard container
echo "1. Stopping dashboard container..."
sudo docker-compose down dashboard 2>/dev/null || true
echo "   ✓ Container stopped"
echo ""

# Remove old dashboard image
echo "2. Removing old dashboard image..."
sudo docker rmi ceres-os-dashboard 2>/dev/null || true
echo "   ✓ Old image removed"
echo ""

# Clear build cache
echo "3. Clearing Docker build cache..."
sudo docker builder prune -f
echo "   ✓ Build cache cleared"
echo ""

# Rebuild without cache
echo "4. Rebuilding dashboard (this may take a few minutes)..."
sudo docker-compose build --no-cache dashboard
echo "   ✓ Dashboard rebuilt"
echo ""

# Start the dashboard
echo "5. Starting dashboard container..."
sudo docker-compose up -d dashboard
echo "   ✓ Dashboard started"
echo ""

# Wait for container to be ready
echo "6. Waiting for dashboard to be ready..."
sleep 5

# Show the new bundle hash
echo "7. Checking new bundle hash..."
NEW_HASH=$(curl -s http://localhost:3000 2>/dev/null | grep -o 'main\.[a-f0-9]*\.js' | head -1 || echo "Could not fetch")
echo "   New bundle: $NEW_HASH"
echo ""

# Show container logs
echo "8. Recent container logs:"
echo "----------------------------------------"
sudo docker-compose logs --tail=20 dashboard
echo "----------------------------------------"
echo ""

echo "========================================="
echo "Rebuild complete!"
echo "Open http://localhost:3000 in your browser"
echo "========================================="
