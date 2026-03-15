#!/bin/bash
#
# OpenClaw Backup Script
# Backs up configuration, workspace, and creates AMI snapshot
#
# Usage: ./backup.sh [--ami]
#

set -e

BACKUP_DIR="${HOME}/openclaw-backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
CREATE_AMI=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --ami)
            CREATE_AMI=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--ami]"
            exit 1
            ;;
    esac
done

echo "🦞 OpenClaw Backup Script"
echo "=========================="
echo ""
echo "Backup directory: $BACKUP_DIR"
echo "Timestamp: $TIMESTAMP"
echo "Create AMI: $CREATE_AMI"
echo ""

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Backup OpenClaw configuration
echo "📦 Backing up OpenClaw configuration..."
if [ -f ~/.openclaw/openclaw.json ]; then
    cp ~/.openclaw/openclaw.json "$BACKUP_DIR/openclaw-config-$TIMESTAMP.json"
    echo "✓ Configuration backed up"
else
    echo "⚠ Configuration file not found"
fi

# Backup workspace
echo ""
echo "📦 Backing up workspace..."
if [ -d ~/.openclaw/workspace ]; then
    tar -czf "$BACKUP_DIR/openclaw-workspace-$TIMESTAMP.tar.gz" \
        -C ~/.openclaw workspace \
        2>/dev/null || echo "⚠ Some files may have been skipped"
    echo "✓ Workspace backed up"
else
    echo "⚠ Workspace directory not found"
fi

# Backup credentials (if any)
echo ""
echo "📦 Backing up credentials..."
if [ -d ~/.openclaw/credentials ]; then
    tar -czf "$BACKUP_DIR/openclaw-credentials-$TIMESTAMP.tar.gz" \
        -C ~/.openclaw credentials \
        2>/dev/null || echo "⚠ Some credential files may have been skipped"
    echo "✓ Credentials backed up"
else
    echo "⚠ Credentials directory not found"
fi

# List backups
echo ""
echo "📋 Backup files created:"
ls -lh "$BACKUP_DIR"/*-$TIMESTAMP* 2>/dev/null || echo "No files created"

# Calculate total size
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
echo ""
echo "Total backup size: $TOTAL_SIZE"

# Create AMI if requested
if [ "$CREATE_AMI" = true ]; then
    echo ""
    echo "📸 Creating AMI snapshot..."
    
    # Get instance ID
    INSTANCE_ID=$(ec2-metadata --instance-id 2>/dev/null | cut -d ' ' -f2)
    
    if [ -z "$INSTANCE_ID" ]; then
        echo "⚠ Could not determine instance ID. Skipping AMI creation."
    else
        echo "Instance ID: $INSTANCE_ID"
        
        AMI_ID=$(aws ec2 create-image \
            --instance-id "$INSTANCE_ID" \
            --name "openclaw-backup-$TIMESTAMP" \
            --description "OpenClaw backup created on $TIMESTAMP" \
            --no-reboot \
            --query 'ImageId' \
            --output text 2>/dev/null)
        
        if [ $? -eq 0 ] && [ -n "$AMI_ID" ]; then
            echo "✓ AMI created: $AMI_ID"
            echo "  Name: openclaw-backup-$TIMESTAMP"
        else
            echo "⚠ AMI creation failed. Check AWS permissions."
        fi
    fi
fi

echo ""
echo "✅ Backup complete!"
echo ""
echo "Backup location: $BACKUP_DIR"
echo ""
echo "To restore:"
echo "  ./scripts/restore.sh $TIMESTAMP"
echo ""
