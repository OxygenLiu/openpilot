#!/usr/bin/env python3
"""
Download openpilot models from GitHub at specific commits
Handles two separate model types:
- Driving Models: driving_vision.onnx + driving_policy.onnx → /data/models/driving/
- Driver Monitoring (DM) Models: dmonitoring_model.onnx → /data/models/dm/

Model registry: /data/models/model_registry.json
"""
import argparse
import json
import requests
import subprocess
from pathlib import Path
from datetime import datetime
from enum import Enum


class ModelType(Enum):
    """Model type enumeration"""
    DRIVING = "driving"
    DM = "dm"


# Model registry location (persists across reboots on C3)
REGISTRY_FILE = Path('/data/models/model_registry.json')


def load_registry():
    """Load model registry from JSON file"""
    if not REGISTRY_FILE.exists():
        print(f"⚠️  Model registry not found: {REGISTRY_FILE}")
        return {}, {}

    with open(REGISTRY_FILE) as f:
        registry = json.load(f)

    return registry.get('driving_models', {}), registry.get('dm_models', {})


def download_file(url: str, dest: Path, desc: str = None):
    """Download file from URL using Git LFS for ONNX files

    Uses git lfs smudge for robust LFS downloads that work with all GitHub models.
    This method supports all LFS transfer adapters (basic, lfs-standalone-file, ssh),
    unlike the batch API which only works for some models.
    """
    print(f"  📥 Downloading {desc or dest.name}...")

    # Download the file (may be LFS pointer or regular file)
    response = requests.get(url)
    response.raise_for_status()
    content = response.content

    # Check if this is a Git LFS pointer file
    if len(content) < 200:
        try:
            lfs_pointer = content.decode('utf-8')
            if lfs_pointer.startswith('version https://git-lfs.github.com'):
                # Parse LFS metadata for display
                lfs_size = None
                for line in lfs_pointer.strip().split('\n'):
                    if line.startswith('size '):
                        lfs_size = int(line.split(' ', 1)[1].strip())
                        break

                if lfs_size:
                    print(f"    🔄 Detected Git LFS file (actual size: {lfs_size / 1024 / 1024:.1f}MB)")
                    print(f"    📦 Using git lfs smudge for download...")

                # Use git lfs smudge to download the actual file
                # This works for ALL models (uses multiple transfer adapters with fallback)
                try:
                    result = subprocess.run(
                        ['git', 'lfs', 'smudge'],
                        input=content,
                        capture_output=True,
                        check=True,
                        timeout=300  # 5 minute timeout
                    )
                    content = result.stdout

                    # Verify we got the actual file, not the pointer
                    if len(content) < 200 or content.startswith(b'version https://git-lfs.github.com'):
                        raise Exception("git lfs smudge returned pointer instead of file")

                    print(f"    ✅ Downloaded via Git LFS ({len(content) / 1024 / 1024:.1f}MB)")

                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
                    raise Exception(f"git lfs smudge failed: {error_msg}")
                except FileNotFoundError:
                    raise Exception("git lfs command not found - please install Git LFS")
        except UnicodeDecodeError:
            # Not a text file, continue with binary download
            pass

    # Write the file
    with open(dest, 'wb') as f:
        f.write(content)

    file_size_mb = dest.stat().st_size / 1024 / 1024
    print(f"    ✅ {dest.name} ({file_size_mb:.1f}MB)")


def check_model_compatibility(model_info: dict, model_type: ModelType) -> tuple[bool, str]:
    """Check if model is compatible with current openpilot version

    Returns:
        (is_compatible, warning_message)
    """
    # Only check driving models (DM models don't use desire_pulse)
    if model_type != ModelType.DRIVING:
        return True, ""

    # Parse model date
    try:
        from datetime import datetime
        model_date = datetime.strptime(model_info['date'], '%Y-%m-%d')
        # desire_pulse transition date: August 27, 2025
        transition_date = datetime(2025, 8, 27)

        if model_date < transition_date:
            warning = (
                f"\n⚠️  COMPATIBILITY WARNING ⚠️\n"
                f"This model was released BEFORE the desire_pulse transition (Aug 27, 2025).\n"
                f"Model date: {model_info['date']}\n"
                f"\n"
                f"Your current openpilot code uses 'desire_pulse' (commit 88e7c48bf).\n"
                f"This model expects 'desire' and will NOT work with your code.\n"
                f"\n"
                f"Compatible models (released after Aug 27, 2025):\n"
                f"  - modeld_desiredesire_pulse_f8ff156\n"
                f"  - firehose_model_f0f04d4\n"
                f"  - nevada_3ca9f35\n"
                f"  - north_nevada_4d08542\n"
                f"  - cool_people_3c957c6 (recommended)\n"
            )
            return False, warning
    except (ValueError, KeyError):
        # If date parsing fails, assume compatible (benefit of doubt)
        pass

    return True, ""


def download_model(model_type: ModelType, model_id: str, output_dir: Path = None):
    """Download a model from openpilot master at specific commit"""

    # Load registry
    driving_models, dm_models = load_registry()

    # Select registry based on type
    if model_type == ModelType.DRIVING:
        registry = driving_models
        type_name = "Driving Model"
        default_dir_name = "models/driving"
    else:
        registry = dm_models
        type_name = "Driver Monitoring Model"
        default_dir_name = "models/dm"

    if model_id not in registry:
        print(f"❌ {type_name} '{model_id}' not found in registry")
        print(f"\nAvailable {type_name.lower()}s:")
        for mid, info in registry.items():
            print(f"  {mid}: {info['name']} ({info['commit']})")
        return 1

    model_info = registry[model_id]

    # Check compatibility
    is_compatible, warning = check_model_compatibility(model_info, model_type)
    if not is_compatible:
        print(warning)
        print("=" * 70)
        response = input("Download anyway? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            print("❌ Download cancelled")
            return 1
        print()

    # Determine output directory
    if output_dir is None:
        if Path('/data').exists():
            output_dir = Path('/data') / default_dir_name / model_id
        else:
            output_dir = Path.home() / 'driving_data' / default_dir_name / model_id

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"Downloading: {model_info['name']} ({type_name})")
    print("=" * 70)
    print(f"Commit: {model_info['commit']}")
    print(f"Date: {model_info['date']}")
    print(f"PR: {model_info.get('pr', 'N/A')}")
    print(f"Description: {model_info['description']}")
    print(f"Output: {output_dir}")
    print()

    # Download ONNX files from GitHub
    base_url = f"https://raw.githubusercontent.com/commaai/openpilot/{model_info['commit']}/selfdrive/modeld/models"

    all_files = model_info['files']

    print(f"ONNX files to download: {len(all_files)}")
    print(f"Type: {type_name}")
    print("(PKL files will be compiled on C3 device)")
    print()

    failed_files = []
    for filename in all_files:
        url = f"{base_url}/{filename}"
        dest = output_dir / filename

        try:
            download_file(url, dest, filename)
        except Exception as e:
            print(f"    ❌ Failed: {e}")
            failed_files.append(filename)

    # Create model_info.json with type information
    info_file = output_dir / 'model_info.json'
    info_data = {
        'name': model_info['name'],
        'version': model_info['commit'],
        'commit': model_info['commit'],
        'date': model_info['date'],
        'pr': model_info.get('pr', ''),
        'description': model_info['description'],
        'source': 'comma.ai',
        'type': model_type.value,
        'downloaded_date': datetime.now().isoformat(),
    }

    with open(info_file, 'w') as f:
        json.dump(info_data, f, indent=2)

    print()
    print("=" * 70)

    if failed_files:
        print(f"⚠️  Download completed with {len(failed_files)} failures:")
        for f in failed_files:
            print(f"  - {f}")
    else:
        print(f"✅ Download complete!")

    print(f"📍 Location: {output_dir}")
    print(f"📄 Metadata: {info_file}")

    # Calculate total size
    total_size = sum(f.stat().st_size for f in output_dir.iterdir() if f.is_file())
    print(f"💾 Total size: {total_size / 1024 / 1024:.1f}MB")

    print()
    print("Next steps:")
    print(f"  1. Verify: python selfdrive/modeld/model_swapper.py --type {model_type.value} verify {model_id}")
    print(f"  2. Swap: python selfdrive/modeld/model_swapper.py --type {model_type.value} swap {model_id}")
    print("=" * 70)

    return 0 if not failed_files else 1


def list_available(model_type: ModelType = None):
    """List all available models in registry"""
    # Load registry
    driving_models, dm_models = load_registry()

    print("=" * 70)
    print("Available Models for Download")
    print("=" * 70)
    print()

    if model_type is None or model_type == ModelType.DRIVING:
        print("[DRIVING MODELS]")
        print("For lateral/longitudinal control (driving_vision.onnx + driving_policy.onnx)")
        print()
        for model_id, info in driving_models.items():
            # Check compatibility
            is_compatible, _ = check_model_compatibility(info, ModelType.DRIVING)
            compat_icon = "✅" if is_compatible else "⚠️"
            compat_text = "Compatible" if is_compatible else "INCOMPATIBLE (pre-desire_pulse)"

            print(f"📦 {model_id}  {compat_icon} {compat_text}")
            print(f"   Name: {info['name']}")
            print(f"   Commit: {info['commit']}")
            print(f"   Date: {info['date']}")
            print(f"   PR: {info.get('pr', 'N/A')}")
            print(f"   Description: {info['description']}")
            print(f"   Files: {len(info['files'])}")
            print()

    if model_type is None or model_type == ModelType.DM:
        print("[DRIVER MONITORING MODELS]")
        print("For driver attention detection (dmonitoring_model.onnx)")
        print()
        for model_id, info in dm_models.items():
            print(f"📦 {model_id}")
            print(f"   Name: {info['name']}")
            print(f"   Commit: {info['commit']}")
            print(f"   Date: {info['date']}")
            print(f"   PR: {info.get('pr', 'N/A')}")
            print(f"   Description: {info['description']}")
            print(f"   Files: {len(info['files'])}")
            print()


def check_updates():
    """Check for new models not yet installed

    Returns JSON with new models available for download
    Filters:
    - Only models >= Firehose (2025-09-05) for v0.10.1+ compatibility
    - Excludes reverted models
    - Excludes already downloaded models
    """
    # Load registry
    driving_models, dm_models = load_registry()

    # Determine base directory
    base_data_dir = Path('/data') if Path('/data').exists() else Path.home() / 'driving_data'

    driving_models_dir = base_data_dir / 'models' / 'driving'
    dm_models_dir = base_data_dir / 'models' / 'dm'

    # Get installed models
    installed_driving = set()
    if driving_models_dir.exists():
        installed_driving = {d.name for d in driving_models_dir.iterdir()
                           if d.is_dir() and not d.name.startswith('_')}

    installed_dm = set()
    if dm_models_dir.exists():
        installed_dm = {d.name for d in dm_models_dir.iterdir()
                       if d.is_dir() and not d.name.startswith('_')}

    # Find new models with filtering
    new_driving = []
    for model_id, info in driving_models.items():
        # Skip if already installed
        if model_id in installed_driving:
            continue

        # FILTER 1: Exclude models older than Firehose (2025-09-05)
        if info.get('date', '9999-99-99') < '2025-09-05':
            continue

        # FILTER 2: Skip reverted models
        if 'revert' in model_id.lower() or 'revert' in info.get('name', '').lower():
            continue

        new_driving.append({
            'id': model_id,
            'type': 'driving',
            **info
        })

    new_dm = []
    for model_id, info in dm_models.items():
        # Skip if already installed
        if model_id in installed_dm:
            continue

        # FILTER 2: Skip reverted models (DM models don't need date filter)
        if 'revert' in model_id.lower() or 'revert' in info.get('name', '').lower():
            continue

        new_dm.append({
            'id': model_id,
            'type': 'dm',
            **info
        })

    # Output as JSON for UI parsing
    result = {
        'driving': new_driving,
        'dm': new_dm,
        'total': len(new_driving) + len(new_dm)
    }

    print(json.dumps(result))
    return 0


def add_model_to_registry(model_type: str, model_id: str, name: str, commit: str,
                          date: str, description: str, pr: str = None):
    """Add a new model to the registry"""

    # Load existing registry
    with open(REGISTRY_FILE) as f:
        registry = json.load(f)

    # Determine model type key and files
    if model_type == 'driving':
        registry_key = 'driving_models'
        files = ['driving_vision.onnx', 'driving_policy.onnx']
    else:
        registry_key = 'dm_models'
        files = ['dmonitoring_model.onnx']

    # Create model entry
    model_entry = {
        'name': name,
        'commit': commit,
        'date': date,
        'description': description,
        'files': files
    }

    if pr:
        model_entry['pr'] = pr

    # Add to registry
    registry[registry_key][model_id] = model_entry
    registry['last_updated'] = datetime.now().strftime('%Y-%m-%d')

    # Save registry
    with open(REGISTRY_FILE, 'w') as f:
        json.dump(registry, f, indent=2)

    print(f"✅ Added {model_type} model '{model_id}' to registry")
    print(f"   Name: {name}")
    print(f"   Commit: {commit}")
    print(f"   Date: {date}")
    if pr:
        print(f"   PR: {pr}")
    print()
    print(f"Registry updated: {REGISTRY_FILE}")

    return 0


def add_model_from_pr(pr_number: int, model_type: str = 'driving'):
    """Add a model to registry by extracting info from GitHub PR

    Args:
        pr_number: GitHub PR number (e.g., 36849)
        model_type: 'driving' or 'dm'
    """
    import re

    print(f"🔍 Fetching PR #{pr_number} from GitHub...")

    api_url = f"https://api.github.com/repos/commaai/openpilot/pulls/{pr_number}"
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        pr = response.json()
    except Exception as e:
        print(f"❌ Failed to fetch PR: {e}")
        return 1

    # Extract info
    title = pr['title']
    merge_commit = pr.get('merge_commit_sha')
    merged_at = pr.get('merged_at')

    if not merge_commit:
        print(f"❌ PR #{pr_number} has not been merged yet")
        return 1

    merged_date = merged_at[:10] if merged_at else datetime.now().strftime('%Y-%m-%d')

    # Generate model_id
    clean_name = title.lower().replace(' ', '_').replace('-', '_')
    clean_name = re.sub(r'[^a-z0-9_]', '', clean_name)
    model_id = f"{clean_name}_{merge_commit[:7]}"

    print(f"✅ Found: {title}")
    print(f"   Commit: {merge_commit[:12]}")
    print(f"   Merged: {merged_date}")
    print()

    # Add to registry
    return add_model_to_registry(
        model_type=model_type,
        model_id=model_id,
        name=title,
        commit=merge_commit,
        date=merged_date,
        description=f"Driving model from PR #{pr_number}",
        pr=f"#{pr_number}"
    )


def update_registry_from_github():
    """Fetch latest model commits from GitHub and update registry

    Three-Layer Filtering System:
    1. Date Filter: Exclude models older than Firehose (2025-09-05) for v0.10.1+ compatibility
    2. Revert Filter: Exclude reverted models and revert commits themselves
       - Detects "Revert" commits and parses which commit was reverted
       - Removes reverted models from registry
    3. Already Downloaded Filter: Applied in check_updates() to show only uninstalled models

    Note: Filter #3 is intentionally in check_updates(), not here, because the registry
    should contain ALL available models. The check_updates() function filters what to
    show users based on what's already installed.
    """

    print("🔍 Checking GitHub for new openpilot models...")

    # Fetch commits from GitHub API
    github_api_url = "https://api.github.com/repos/commaai/openpilot/commits"
    params = {
        'path': 'selfdrive/modeld/models',
        'per_page': 30  # Check last 30 commits to catch reverts
    }

    try:
        response = requests.get(github_api_url, params=params)
        response.raise_for_status()
        commits_data = response.json()
    except Exception as e:
        print(f"❌ Failed to fetch commits from GitHub: {e}")
        return 1

    # Load existing registry
    with open(REGISTRY_FILE) as f:
        registry = json.load(f)

    existing_commits = set()
    for models_dict in [registry['driving_models'], registry['dm_models']]:
        for model_info in models_dict.values():
            existing_commits.add(model_info['commit'])

    # PHASE 1: Parse all commits to find reverted commit hashes
    import re
    reverted_commits = set()

    for commit_data in commits_data:
        commit_message = commit_data['commit']['message']

        # Check if this is a revert commit
        if commit_message.split('\n')[0].lower().startswith('revert'):
            # Parse commit message to extract reverted commit hash
            # Format: "This reverts commit <hash>."
            revert_match = re.search(r'reverts commit ([0-9a-f]{40})', commit_message, re.IGNORECASE)
            if revert_match:
                reverted_hash = revert_match.group(1)
                reverted_commits.add(reverted_hash)
                print(f"  🔍 Found revert: {reverted_hash[:12]} was reverted")

    # PHASE 2: Remove reverted models from registry
    models_removed = 0
    for registry_key in ['driving_models', 'dm_models']:
        models_to_remove = []
        for model_id, model_info in registry[registry_key].items():
            if model_info['commit'] in reverted_commits:
                models_to_remove.append(model_id)
                print(f"  🗑️  Removing reverted model: {model_id} (commit {model_info['commit'][:12]})")

        for model_id in models_to_remove:
            del registry[registry_key][model_id]
            models_removed += 1

    new_models_added = 0

    # PHASE 3: Parse commits for new model updates
    for commit_data in commits_data:
        commit_hash = commit_data['sha']
        commit_hash_short = commit_hash[:7]
        commit_message = commit_data['commit']['message']
        commit_date = commit_data['commit']['committer']['date'][:10]  # YYYY-MM-DD

        # Skip if already in registry
        if commit_hash in existing_commits:
            continue

        # FILTER 1: Exclude models older than Firehose model (2025-09-05)
        # Only include models from Firehose onwards for v0.10.1+ compatibility
        if commit_date < "2025-09-05":
            continue

        # FILTER 2a: Exclude revert commits themselves
        # Only skip commits that ARE revert commits (title starts with "Revert")
        if commit_message.split('\n')[0].lower().startswith('revert'):
            continue

        # FILTER 2b: Exclude commits that were later reverted
        if commit_hash in reverted_commits:
            continue

        # Parse commit message for model info
        # Expected format: "Model Name 🎯 (#12345)"
        if '(#' not in commit_message:
            continue

        # Extract PR number
        pr_match = commit_message.find('(#')
        if pr_match == -1:
            continue

        pr_end = commit_message.find(')', pr_match)
        pr_number = commit_message[pr_match:pr_end+1]
        model_name = commit_message[:pr_match].strip()

        # Determine model type
        if 'DM:' in model_name or 'dmonitoring' in commit_message.lower():
            model_type = 'dm'
            model_name = model_name.replace('DM:', '').strip()
            registry_key = 'dm_models'
            files = ['dmonitoring_model.onnx']
        else:
            model_type = 'driving'
            registry_key = 'driving_models'
            files = ['driving_vision.onnx', 'driving_policy.onnx']

        # Generate model ID - clean up name and append commit hash
        import re
        clean_name = model_name.lower().replace(' ', '_')
        clean_name = re.sub(r'[^a-z0-9_]', '', clean_name)
        model_id = f"{clean_name}_{commit_hash_short}"

        # Create model entry
        model_entry = {
            'name': model_name,
            'commit': commit_hash,
            'date': commit_date,
            'description': f'Model from {commit_date}',
            'pr': pr_number,
            'files': files
        }

        # Add to registry
        registry[registry_key][model_id] = model_entry
        new_models_added += 1

        print(f"✅ Found new {model_type} model: {model_name}")
        print(f"   ID: {model_id}")
        print(f"   Commit: {commit_hash_short}")
        print(f"   Date: {commit_date}")
        print(f"   PR: {pr_number}")
        print()

    if new_models_added > 0 or models_removed > 0:
        # Update last_updated timestamp
        registry['last_updated'] = datetime.now().strftime('%Y-%m-%d')

        # Save updated registry
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(registry, f, indent=2)

        if new_models_added > 0:
            print(f"✅ Added {new_models_added} new model(s) to registry")
        if models_removed > 0:
            print(f"🗑️  Removed {models_removed} reverted model(s) from registry")
        print(f"📄 Registry updated: {REGISTRY_FILE}")
    else:
        print("✅ Registry is up to date - no new models found, no reverted models detected")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Download openpilot models from GitHub (separated driving/DM models)'
    )
    parser.add_argument('action', choices=['list', 'download', 'check-updates', 'add-model', 'add-from-pr', 'update-registry'],
                       help='Action to perform')
    parser.add_argument('--type', choices=['driving', 'dm'],
                       help='Model type: driving or dm (driver monitoring)')
    parser.add_argument('model_id', nargs='?',
                       help='Model ID to download/add, or PR number for add-from-pr')
    parser.add_argument('--output', '-o', type=Path,
                       help='Output directory (default: /data/models/ or /data/dm-models/)')

    # Arguments for add-model command
    parser.add_argument('--name', help='Model display name (for add-model)')
    parser.add_argument('--commit', help='Full GitHub commit hash (for add-model)')
    parser.add_argument('--date', help='Release date YYYY-MM-DD (for add-model)')
    parser.add_argument('--description', help='Model description (for add-model)')
    parser.add_argument('--pr', help='PR number like #36249 (for add-model)')

    args = parser.parse_args()

    if args.action == 'list':
        model_type = ModelType.DRIVING if args.type == 'driving' else (ModelType.DM if args.type == 'dm' else None)
        list_available(model_type)
        return 0

    elif args.action == 'check-updates':
        return check_updates()

    elif args.action == 'download':
        if not args.model_id:
            print("❌ model_id required for download")
            return 1

        if not args.type:
            print("❌ --type required for download (driving or dm)")
            print()
            print("Examples:")
            print("  python download_openpilot_models.py download --type driving cool_people_3c957c6")
            print("  python download_openpilot_models.py download --type dm medium_fanta_cc8f6ea")
            return 1

        model_type = ModelType.DRIVING if args.type == 'driving' else ModelType.DM

        return download_model(model_type, args.model_id, args.output)

    elif args.action == 'add-model':
        if not all([args.model_id, args.type, args.name, args.commit, args.date, args.description]):
            print("❌ add-model requires: model_id, --type, --name, --commit, --date, --description")
            print()
            print("Example:")
            print("  python download_openpilot_models.py add-model cool_people_3c957c6 \\")
            print("    --type driving \\")
            print("    --name \"The Cool People's Model 😎\" \\")
            print("    --commit 3c957c6e9d8f05138b8a80523d50db5b5ca2cb73 \\")
            print("    --date 2025-10-20 \\")
            print("    --description \"Latest driving model with improved vision\" \\")
            print("    --pr \"#36249\"")
            return 1

        return add_model_to_registry(args.type, args.model_id, args.name, args.commit,
                                    args.date, args.description, args.pr)

    elif args.action == 'add-from-pr':
        if not args.model_id:
            print("❌ PR number required for add-from-pr")
            print()
            print("Example:")
            print("  python download_openpilot_models.py add-from-pr 36849")
            print("  python download_openpilot_models.py add-from-pr 36849 --type dm")
            return 1

        # Extract PR number (handle URLs or plain numbers)
        pr_input = args.model_id
        if 'github.com' in pr_input:
            # Extract from URL like https://github.com/commaai/openpilot/pull/36849
            import re
            match = re.search(r'/pull/(\d+)', pr_input)
            if match:
                pr_number = int(match.group(1))
            else:
                print(f"❌ Could not extract PR number from URL: {pr_input}")
                return 1
        else:
            try:
                pr_number = int(pr_input.replace('#', ''))
            except ValueError:
                print(f"❌ Invalid PR number: {pr_input}")
                return 1

        model_type = args.type or 'driving'
        return add_model_from_pr(pr_number, model_type)

    elif args.action == 'update-registry':
        return update_registry_from_github()


if __name__ == '__main__':
    import sys
    sys.exit(main())
