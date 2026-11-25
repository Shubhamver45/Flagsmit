#!/usr/bin/env python3
"""
scripts/check_readme_links.py - Validates that all relative links and images in README.md resolve.
"""

import os
import re
import sys

def check_readme_links(readme_path: str = "README.md") -> bool:
    if not os.path.exists(readme_path):
        print(f"Error: {readme_path} not found.", file=sys.stderr)
        return False

    with open(readme_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all targets inside ](url)
    targets = re.findall(r'\]\(([^)]+)\)', content)

    print(f"Verifying relative links and images in {readme_path}...\n")
    print(f"{'Path / URL':<42} | {'Kind':<14} | {'Status':<10}")
    print(f"{'-'*42}-|-{'-'*14}-|-{'-'*10}")

    all_ok = True
    seen = set()
    for raw in targets:
        # Ignore external URLs and internal anchor fragments
        if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("#"):
            continue

        clean_path = raw.split("#")[0]
        if clean_path in seen:
            continue
        seen.add(clean_path)

        exists = os.path.exists(clean_path)
        if clean_path.endswith((".png", ".jpg", ".svg", ".gif")):
            kind = "Image"
        elif os.path.isdir(clean_path):
            kind = "Directory"
        else:
            kind = "Markdown/File"

        status = "EXISTS" if exists else "MISSING"
        print(f"{clean_path:<42} | {kind:<14} | {status:<10}")
        if not exists:
            all_ok = False

    print("\nResult: " + ("ALL RELATIVE LINKS RESOLVE OK" if all_ok else "FAILED: BROKEN LINKS FOUND"))
    return all_ok

if __name__ == "__main__":
    success = check_readme_links()
    sys.exit(0 if success else 1)
