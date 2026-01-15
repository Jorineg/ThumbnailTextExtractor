#!/bin/sh
# QCAD Watcher - monitors /dwg-exchange for conversion requests
# Runs inside QCAD container (also air-gapped)
#
# Protocol:
# 1. Processor writes {job_id}.dwg and {job_id}.convert
# 2. This script sees .convert, runs conversion
# 3. Creates {job_id}.done or {job_id}.failed

EXCHANGE_DIR="/dwg-exchange"

echo "QCAD Watcher starting..."
echo "Watching: $EXCHANGE_DIR"

while true; do
    for convert_file in "$EXCHANGE_DIR"/*.convert; do
        [ -e "$convert_file" ] || continue
        
        job_id=$(basename "$convert_file" .convert)
        dwg_file=$(cat "$convert_file")
        dwg_path="$EXCHANGE_DIR/$dwg_file"
        pdf_path="$EXCHANGE_DIR/${job_id}.pdf"
        done_file="$EXCHANGE_DIR/${job_id}.done"
        failed_file="$EXCHANGE_DIR/${job_id}.failed"
        
        echo "Converting: $dwg_file -> ${job_id}.pdf"
        
        if [ ! -f "$dwg_path" ]; then
            echo "Error: DWG file not found: $dwg_path"
            echo "DWG file not found" > "$failed_file"
            rm -f "$convert_file"
            continue
        fi
        
        # Run dwg2pdf conversion
        /exec/qcad/dwg2pdf -a -auto-orientation -f -o "$pdf_path" "$dwg_path" 2>&1
        result=$?
        
        # Remove signal file
        rm -f "$convert_file"
        
        if [ $result -eq 0 ] && [ -f "$pdf_path" ]; then
            echo "Success: ${job_id}.pdf"
            touch "$done_file"
        else
            echo "Failed: conversion error (exit code: $result)"
            echo "Conversion failed with exit code $result" > "$failed_file"
        fi
    done
    
    sleep 0.5
done

