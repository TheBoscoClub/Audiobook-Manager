#!/usr/bin/env python3
"""
R&D Script: Audible Position Sync Testing

This script explores bidirectional playback position sync between
Audible's cloud and a local audiobook library.

API Endpoints Used:
  - GET  /1.0/annotations/lastpositions?asins=...  (read positions)
  - POST /1.0/content/{asin}/licenserequest        (get acr for writes)
  - PUT  /1.0/lastpositions/{asin}                 (write position)

Usage:
  python position_sync_test.py --help
  python position_sync_test.py read <asin>
  python position_sync_test.py write <asin> <position_ms>
  python position_sync_test.py sync <asin> <local_position_ms>
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from getpass import getpass

try:
    import audible
except ImportError:
    print("ERROR: 'audible' library not installed. Run: pip install audible")
    sys.exit(1)

# Import credential manager (same directory)
from credential_manager import get_or_prompt_credential, has_stored_credential, retrieve_credential


# Configuration - use real user's home even when running as sudo
REAL_USER_HOME = Path(os.environ.get("HOME", "/home/bosco"))
if os.environ.get("SUDO_USER"):
    REAL_USER_HOME = Path(f"/home/{os.environ['SUDO_USER']}")
AUDIBLE_CONFIG_DIR = REAL_USER_HOME / ".audible"
AUTH_FILE = AUDIBLE_CONFIG_DIR / "audible.json"
CREDENTIAL_FILE_PATH = AUDIBLE_CONFIG_DIR / "position_sync_credentials.enc"
COUNTRY_CODE = "us"


def ms_to_human(ms: int) -> str:
    """Convert milliseconds to human-readable format."""
    if ms is None:
        return "N/A"
    seconds = ms // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def human_to_ms(human: str) -> int:
    """Convert human-readable time to milliseconds.

    Accepts formats like: "1h 30m 45s", "45m 30s", "120s", "5400000" (raw ms)
    """
    # If it's just digits, assume milliseconds
    if human.isdigit():
        return int(human)

    total_ms = 0
    import re

    # Match hours, minutes, seconds
    hours = re.search(r'(\d+)\s*h', human, re.IGNORECASE)
    minutes = re.search(r'(\d+)\s*m(?!s)', human, re.IGNORECASE)  # m but not ms
    seconds = re.search(r'(\d+)\s*s', human, re.IGNORECASE)

    if hours:
        total_ms += int(hours.group(1)) * 3600 * 1000
    if minutes:
        total_ms += int(minutes.group(1)) * 60 * 1000
    if seconds:
        total_ms += int(seconds.group(1)) * 1000

    return total_ms


async def get_authenticated_client(password: str = None) -> audible.AsyncClient:
    """Create an authenticated Audible client."""
    if not AUTH_FILE.exists():
        print(f"ERROR: Auth file not found: {AUTH_FILE}")
        print("Run 'audible quickstart' to set up authentication first.")
        sys.exit(1)

    try:
        auth = audible.Authenticator.from_file(
            AUTH_FILE,
            password=password
        )
        return audible.AsyncClient(auth=auth, country_code=COUNTRY_CODE)
    except Exception as e:
        print(f"ERROR: Failed to authenticate: {e}")
        sys.exit(1)


async def read_position(client: audible.AsyncClient, asin: str) -> dict:
    """
    Read the last playback position for an audiobook from Audible.

    Returns dict with position_ms, last_updated, and raw response.
    """
    print(f"\n📖 Reading position for ASIN: {asin}")
    print("-" * 50)

    try:
        # Method 1: Try annotations/lastpositions endpoint
        response = await client.get(
            f"1.0/annotations/lastpositions",
            params={"asins": asin}
        )

        print(f"Raw response: {json.dumps(response, indent=2, default=str)}")

        # Parse position from response
        result = {
            "asin": asin,
            "position_ms": None,
            "position_human": None,
            "last_updated": None,
            "status": None,
            "raw_response": response
        }

        # Extract from asin_last_position_heard_annots structure
        annotations = response.get("asin_last_position_heard_annots", [])
        for annot in annotations:
            if annot.get("asin") == asin:
                pos_data = annot.get("last_position_heard", {})
                result["position_ms"] = pos_data.get("position_ms")
                result["position_human"] = ms_to_human(result["position_ms"])
                result["last_updated"] = pos_data.get("last_updated")
                result["status"] = pos_data.get("status")
                break

        # Print parsed summary
        if result["position_ms"]:
            print(f"\n✅ Position found:")
            print(f"   Position: {result['position_human']} ({result['position_ms']} ms)")
            print(f"   Last updated: {result['last_updated']}")
            print(f"   Status: {result['status']}")
        else:
            print(f"\n⚠️  No position found for this ASIN")

        return result

    except Exception as e:
        print(f"ERROR reading position: {e}")
        return {"asin": asin, "error": str(e)}


async def get_content_license(client: audible.AsyncClient, asin: str) -> dict:
    """
    Get content license including ACR (needed for position writes).

    The ACR (Audible Content Reference) is required to update positions.
    """
    print(f"\n🔑 Getting content license for ASIN: {asin}")
    print("-" * 50)

    try:
        # Request license with last_position_heard response group
        # Note: Adrm requires Download consumption_type, not Streaming
        response = await client.post(
            f"1.0/content/{asin}/licenserequest",
            body={
                "drm_type": "Adrm",
                "consumption_type": "Download",
                "quality": "High",
                "response_groups": "last_position_heard,chapter_info,content_reference"
            }
        )

        print(f"License response keys: {list(response.keys()) if isinstance(response, dict) else 'N/A'}")

        # Debug: print full response structure
        print(f"\nFull response:\n{json.dumps(response, indent=2, default=str)[:2000]}")

        content_license = response.get("content_license", {})

        result = {
            "asin": asin,
            "acr": content_license.get("acr"),
            "license_id": content_license.get("license_id"),
            "last_position_heard": response.get("last_position_heard"),
            "content_license_keys": list(content_license.keys()) if isinstance(content_license, dict) else None,
        }

        print(f"\n📋 License details:")
        print(f"   ACR: {result['acr'][:50] + '...' if result['acr'] else 'Not found'}")
        print(f"   License ID: {result['license_id']}")
        print(f"   Content license keys: {result['content_license_keys']}")

        if result["last_position_heard"]:
            pos_ms = result["last_position_heard"].get("position_ms")
            print(f"   Last position heard: {ms_to_human(pos_ms)} ({pos_ms} ms)")
        else:
            print(f"   Last position heard: Not in response")

        return result

    except Exception as e:
        print(f"ERROR getting license: {e}")
        return {"asin": asin, "error": str(e)}


async def write_position(
    client: audible.AsyncClient,
    asin: str,
    position_ms: int,
    acr: str = None
) -> dict:
    """
    Write/update playback position for an audiobook on Audible.

    Requires ACR from content license request.
    """
    print(f"\n✏️  Writing position for ASIN: {asin}")
    print(f"   Position: {ms_to_human(position_ms)} ({position_ms} ms)")
    print("-" * 50)

    # Get ACR if not provided
    if not acr:
        print("   Getting ACR from license request...")
        license_info = await get_content_license(client, asin)
        acr = license_info.get("acr")

        if not acr:
            return {
                "asin": asin,
                "success": False,
                "error": "Could not obtain ACR for position update"
            }

    try:
        response = await client.put(
            f"1.0/lastpositions/{asin}",
            body={
                "acr": acr,
                "asin": asin,
                "position_ms": position_ms
            }
        )

        print(f"Write response: {json.dumps(response, indent=2, default=str)}")

        return {
            "asin": asin,
            "success": True,
            "position_ms": position_ms,
            "response": response
        }

    except Exception as e:
        print(f"ERROR writing position: {e}")
        return {"asin": asin, "success": False, "error": str(e)}


async def sync_position(
    client: audible.AsyncClient,
    asin: str,
    local_position_ms: int
) -> dict:
    """
    Bidirectional sync: compare local and cloud positions, update whichever is behind.

    Logic: "Furthest ahead wins"
    - If cloud > local: return cloud position (local should update)
    - If local > cloud: push local to cloud
    - If equal: no action needed
    """
    print(f"\n🔄 Syncing position for ASIN: {asin}")
    print(f"   Local position: {ms_to_human(local_position_ms)} ({local_position_ms} ms)")
    print("-" * 50)

    # Get cloud position via license request (includes ACR for potential write)
    license_info = await get_content_license(client, asin)

    cloud_position_ms = None
    if license_info.get("last_position_heard"):
        cloud_position_ms = license_info["last_position_heard"].get("position_ms", 0)

    print(f"   Cloud position: {ms_to_human(cloud_position_ms)} ({cloud_position_ms} ms)")

    result = {
        "asin": asin,
        "local_position_ms": local_position_ms,
        "cloud_position_ms": cloud_position_ms,
        "action": None,
        "final_position_ms": None
    }

    # Compare and sync
    if cloud_position_ms is None:
        print("   ⚠️  No cloud position found - pushing local to cloud")
        write_result = await write_position(client, asin, local_position_ms, license_info.get("acr"))
        result["action"] = "pushed_to_cloud"
        result["final_position_ms"] = local_position_ms
        result["write_result"] = write_result

    elif cloud_position_ms > local_position_ms:
        diff = cloud_position_ms - local_position_ms
        print(f"   ☁️  Cloud is ahead by {ms_to_human(diff)} - local should update")
        result["action"] = "pull_from_cloud"
        result["final_position_ms"] = cloud_position_ms

    elif local_position_ms > cloud_position_ms:
        diff = local_position_ms - cloud_position_ms
        print(f"   💾 Local is ahead by {ms_to_human(diff)} - pushing to cloud")
        write_result = await write_position(client, asin, local_position_ms, license_info.get("acr"))
        result["action"] = "pushed_to_cloud"
        result["final_position_ms"] = local_position_ms
        result["write_result"] = write_result

    else:
        print("   ✅ Positions are in sync!")
        result["action"] = "already_synced"
        result["final_position_ms"] = local_position_ms

    return result


async def list_library_sample(client: audible.AsyncClient, limit: int = 5):
    """List a sample of library items to get ASINs for testing."""
    print(f"\n📚 Fetching {limit} library items for testing...")
    print("-" * 50)

    try:
        library = await client.get(
            "1.0/library",
            params={
                "num_results": limit,
                "response_groups": "product_desc,product_attrs",
                "sort_by": "-PurchaseDate"
            }
        )

        items = library.get("items", [])
        print(f"Found {len(items)} items:\n")

        for item in items:
            asin = item.get("asin", "N/A")
            title = item.get("title", "Unknown")[:50]
            print(f"  ASIN: {asin}")
            print(f"  Title: {title}")
            print()

        return items

    except Exception as e:
        print(f"ERROR listing library: {e}")
        return []


async def batch_sync_from_db(client: audible.AsyncClient, db_path: str, limit: int = None):
    """
    Batch sync positions for all audiobooks in the local database that have ASINs.

    Syncs with Audible using "furthest ahead wins" logic.
    """
    import sqlite3

    print(f"\n🔄 Batch Sync from Database")
    print("=" * 60)

    # Connect to database
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all books with ASINs
    query = """
        SELECT id, title, asin, playback_position_ms, duration_hours
        FROM audiobooks
        WHERE asin IS NOT NULL AND asin != ''
    """
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    books = cursor.fetchall()

    print(f"📖 Found {len(books)} syncable audiobooks")

    if not books:
        print("No books to sync!")
        return

    # Batch fetch positions from Audible
    asins = [b['asin'] for b in books]
    asin_to_book = {b['asin']: dict(b) for b in books}

    print(f"☁️  Fetching positions from Audible...")

    # Fetch in batches - API limit: max 25 ASINs per request
    batch_size = 25
    all_positions = {}

    for i in range(0, len(asins), batch_size):
        batch = asins[i:i+batch_size]
        try:
            response = await client.get(
                "1.0/annotations/lastpositions",
                params={"asins": ",".join(batch)}
            )

            for annot in response.get("asin_last_position_heard_annots", []):
                asin = annot.get("asin")
                pos_data = annot.get("last_position_heard", {})
                all_positions[asin] = {
                    "position_ms": pos_data.get("position_ms"),
                    "last_updated": pos_data.get("last_updated"),
                    "status": pos_data.get("status"),
                }
        except Exception as e:
            print(f"   Warning: Batch fetch error: {e}")

        print(f"   Fetched {min(i+batch_size, len(asins))}/{len(asins)}...")

    print(f"✅ Got positions for {len(all_positions)} books")

    # Analyze and sync
    results = {
        "pulled": [],      # Audible ahead -> update local
        "pushed": [],      # Local ahead -> update Audible
        "synced": [],      # Already in sync
        "no_audible": [],  # No position on Audible
        "errors": [],
    }

    now = datetime.now().isoformat()

    for asin, book in asin_to_book.items():
        local_pos = book['playback_position_ms'] or 0
        audible_data = all_positions.get(asin, {})
        audible_pos = audible_data.get("position_ms") or 0

        result = {
            "id": book['id'],
            "title": book['title'][:40],
            "asin": asin,
            "local_ms": local_pos,
            "audible_ms": audible_pos,
        }

        if audible_pos == 0 and local_pos == 0:
            results["synced"].append(result)
        elif audible_pos > local_pos:
            results["pulled"].append(result)
            # Update local database
            cursor.execute("""
                UPDATE audiobooks
                SET playback_position_ms = ?,
                    playback_position_updated = ?,
                    audible_position_ms = ?,
                    audible_position_updated = ?,
                    position_synced_at = ?
                WHERE id = ?
            """, (audible_pos, now, audible_pos, now, now, book['id']))
        elif local_pos > audible_pos:
            results["pushed"].append(result)
            # Push to Audible
            try:
                push_result = await push_audible_position(client, asin, local_pos)
                if not push_result.get("success"):
                    results["errors"].append(result)
            except Exception as e:
                result["error"] = str(e)
                results["errors"].append(result)
            # Update local tracking
            cursor.execute("""
                UPDATE audiobooks
                SET audible_position_ms = ?,
                    audible_position_updated = ?,
                    position_synced_at = ?
                WHERE id = ?
            """, (local_pos, now, now, book['id']))
        else:
            results["synced"].append(result)
            # Just update sync timestamp
            cursor.execute("""
                UPDATE audiobooks
                SET audible_position_ms = ?,
                    position_synced_at = ?
                WHERE id = ?
            """, (audible_pos, now, book['id']))

    conn.commit()
    conn.close()

    # Print summary
    print(f"\n📊 Sync Results:")
    print(f"   ☁️→💾 Pulled from Audible: {len(results['pulled'])}")
    print(f"   💾→☁️ Pushed to Audible:  {len(results['pushed'])}")
    print(f"   ✅ Already synced:        {len(results['synced'])}")
    print(f"   ❌ Errors:                {len(results['errors'])}")

    # Show samples
    if results["pulled"]:
        print(f"\n📋 Sample Pulled (Audible → Local):")
        for r in results["pulled"][:5]:
            print(f"   {r['title']}: {ms_to_human(r['audible_ms'])}")

    if results["pushed"]:
        print(f"\n📋 Sample Pushed (Local → Audible):")
        for r in results["pushed"][:5]:
            print(f"   {r['title']}: {ms_to_human(r['local_ms'])}")

    return results


async def push_audible_position(client: audible.AsyncClient, asin: str, position_ms: int) -> dict:
    """Push position to Audible (helper for batch sync)."""
    try:
        # Get ACR
        license_response = await client.post(
            f"1.0/content/{asin}/licenserequest",
            body={
                "drm_type": "Adrm",
                "consumption_type": "Download",
                "quality": "High",
            }
        )

        content_license = license_response.get("content_license", {})
        acr = content_license.get("acr")

        if not acr:
            return {"success": False, "error": "No ACR"}

        # Push position
        await client.put(
            f"1.0/lastpositions/{asin}",
            body={"acr": acr, "asin": asin, "position_ms": position_ms}
        )

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def main():
    parser = argparse.ArgumentParser(
        description="R&D: Test Audible position sync API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s list                        # List library items to get ASINs
  %(prog)s read B017V4IMVQ             # Read position for an ASIN
  %(prog)s write B017V4IMVQ 3600000    # Write position (1 hour in ms)
  %(prog)s write B017V4IMVQ "1h 30m"   # Write position (human format)
  %(prog)s sync B017V4IMVQ 5400000     # Sync with local at 1.5 hours
        """
    )

    parser.add_argument(
        "command",
        choices=["list", "read", "write", "sync", "license", "batch-sync"],
        help="Command to execute"
    )
    parser.add_argument(
        "--db",
        default="/var/lib/audiobooks/audiobooks.db",
        help="Database path for batch-sync (default: /var/lib/audiobooks/audiobooks.db)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of books to sync (for testing)"
    )
    parser.add_argument(
        "asin",
        nargs="?",
        help="Audiobook ASIN (Amazon Standard Identification Number)"
    )
    parser.add_argument(
        "position",
        nargs="?",
        help="Position in ms or human format (e.g., '1h 30m 45s')"
    )
    parser.add_argument(
        "--password", "-p",
        help="Password for encrypted auth file (uses stored credential if available)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--reset-credential",
        action="store_true",
        help="Force re-entry of Audible password (clears stored credential)"
    )
    parser.add_argument(
        "--master-password", "-m",
        default="",
        help="Master password for credential encryption (default: empty)"
    )

    args = parser.parse_args()

    # Validate arguments
    if args.command in ["read", "write", "sync", "license"] and not args.asin:
        parser.error(f"'{args.command}' command requires an ASIN")

    if args.command in ["write", "sync"] and not args.position:
        parser.error(f"'{args.command}' command requires a position")

    # Get password: CLI arg > env var > stored credential > interactive prompt
    password = args.password or os.environ.get("AUDIBLE_PASSWORD")

    if not password:
        # Use credential manager with correct path (handles sudo)
        password = retrieve_credential(credential_file=CREDENTIAL_FILE_PATH)
        if not password:
            # Fall back to prompting (for first-time setup)
            password = get_or_prompt_credential(
                master_password=args.master_password,
                force_prompt=args.reset_credential
            )
        if not password:
            print("ERROR: No password provided")
            sys.exit(1)
        print(f"🔓 Using credential from {CREDENTIAL_FILE_PATH}")

    # Execute command
    async with await get_authenticated_client(password) as client:
        if args.command == "list":
            result = await list_library_sample(client)

        elif args.command == "read":
            result = await read_position(client, args.asin)

        elif args.command == "license":
            result = await get_content_license(client, args.asin)

        elif args.command == "write":
            position_ms = human_to_ms(args.position)
            result = await write_position(client, args.asin, position_ms)

        elif args.command == "sync":
            position_ms = human_to_ms(args.position)
            result = await sync_position(client, args.asin, position_ms)

        elif args.command == "batch-sync":
            result = await batch_sync_from_db(client, args.db, args.limit)

        if args.json and result:
            print("\n" + "=" * 50)
            print("JSON Output:")
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
