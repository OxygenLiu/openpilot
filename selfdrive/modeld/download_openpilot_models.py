#!/usr/bin/env python3
"""
Download openpilot models from GitHub at specific commits
Handles two separate model types:
- Driving Models: driving_vision.onnx + driving_policy.onnx
- Driver Monitoring (DM) Models: dmonitoring_model.onnx
"""
import argparse
import json
import requests
from pathlib import Path
from datetime import datetime
from enum import Enum


class ModelType(Enum):
    """Model type enumeration"""
    DRIVING = "driving"
    DM = "dm"


# Model registry location
REGISTRY_FILE = Path(__file__).parent / 'model_registry.json'


def load_registry():
    """Load model registry from JSON file"""
    if not REGISTRY_FILE.exists():
        print(f"⚠️  Model registry not found: {REGISTRY_FILE}")
        return {}, {}

    with open(REGISTRY_FILE) as f:
        registry = json.load(f)

    return registry.get('driving_models', {}), registry.get('dm_models', {})


def download_file(url: str, dest: Path, desc: str = None):
    """Download file from URL with progress, handling Git LFS"""
    print(f"  📥 Downloading {desc or dest.name}...")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    # Check if this is a Git LFS pointer file
    content_type = response.headers.get('content-type', '')
    content_length = int(response.headers.get('content-length', 0))

    # Small text files are likely LFS pointers
    if content_length < 200 and 'text/plain' in content_type:
        # Read the potential LFS pointer
        lfs_pointer = response.content.decode('utf-8')

        if lfs_pointer.startswith('version https://git-lfs.github.com'):
            # Parse LFS pointer
            lines = lfs_pointer.strip().split('\n')
            lfs_oid = None
            lfs_size = None

            for line in lines:
                if line.startswith('oid sha256:'):
                    lfs_oid = line.split(':', 1)[1].strip()
                elif line.startswith('size '):
                    lfs_size = int(line.split(' ', 1)[1].strip())

            if lfs_oid:
                print(f"    🔄 Detected Git LFS file (actual size: {lfs_size / 1024 / 1024:.1f}MB)")

                # Download from LFS endpoint
                lfs_url = f"https://github.com/commaai/openpilot.git/info/lfs/objects/batch"
                lfs_request = {
                    "operation": "download",
                    "transfers": ["basic"],
                    "objects": [{"oid": lfs_oid, "size": lfs_size}]
                }

                lfs_response = requests.post(
                    lfs_url,
                    json=lfs_request,
                    headers={
                        'Accept': 'application/vnd.git-lfs+json',
                        'Content-Type': 'application/vnd.git-lfs+json'
                    }
                )
                lfs_response.raise_for_status()

                lfs_data = lfs_response.json()
                download_url = lfs_data['objects'][0]['actions']['download']['href']

                # Download actual file
                response = requests.get(download_url, stream=True)
                response.raise_for_status()
                content_length = lfs_size

    total_size = content_length

    with open(dest, 'wb') as f:
        if total_size == 0:
            f.write(response.content)
        else:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                # Simple progress indicator
                percent = (downloaded / total_size) * 100
                if downloaded % (1024 * 1024) == 0:  # Every 1MB
                    print(f"    {percent:.1f}% ({downloaded / 1024 / 1024:.1f}MB / {total_size / 1024 / 1024:.1f}MB)")

    print(f"    ✅ {dest.name} ({dest.stat().st_size / 1024 / 1024:.1f}MB)")


def download_model(model_type: ModelType, model_id: str, output_dir: Path = None):
    """Download a model from openpilot master at specific commit"""

    # Load registry
    driving_models, dm_models = load_registry()

    # Select registry based on type
    if model_type == ModelType.DRIVING:
        registry = driving_models
        type_name = "Driving Model"
        default_dir_name = "models"
    else:
        registry = dm_models
        type_name = "Driver Monitoring Model"
        default_dir_name = "dm-models"

    if model_id not in registry:
        print(f"❌ {type_name} '{model_id}' not found in registry")
        print(f"\nAvailable {type_name.lower()}s:")
        for mid, info in registry.items():
            print(f"  {mid}: {info['name']} ({info['commit']})")
        return 1

    model_info = registry[model_id]

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
            print(f"📦 {model_id}")
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
    """
    # Load registry
    driving_models, dm_models = load_registry()

    # Determine base directory
    base_data_dir = Path('/data') if Path('/data').exists() else Path.home() / 'driving_data'

    driving_models_dir = base_data_dir / 'models'
    dm_models_dir = base_data_dir / 'dm-models'

    # Get installed models
    installed_driving = set()
    if driving_models_dir.exists():
        installed_driving = {d.name for d in driving_models_dir.iterdir()
                           if d.is_dir() and not d.name.startswith('_')}

    installed_dm = set()
    if dm_models_dir.exists():
        installed_dm = {d.name for d in dm_models_dir.iterdir()
                       if d.is_dir() and not d.name.startswith('_')}

    # Find new models
    new_driving = []
    for model_id, info in driving_models.items():
        if model_id not in installed_driving:
            new_driving.append({
                'id': model_id,
                'type': 'driving',
                **info
            })

    new_dm = []
    for model_id, info in dm_models.items():
        if model_id not in installed_dm:
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


def update_registry_from_github():
    """Fetch latest model commits from GitHub and update registry"""

    print("🔍 Checking GitHub for new openpilot models...")

    # Fetch commits from GitHub API
    github_api_url = "https://api.github.com/repos/commaai/openpilot/commits"
    params = {
        'path': 'selfdrive/modeld/models',
        'per_page': 20  # Check last 20 commits
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

    new_models_added = 0

    # Parse commits for model updates
    for commit_data in commits_data:
        commit_hash = commit_data['sha']
        commit_hash_short = commit_hash[:7]
        commit_message = commit_data['commit']['message']
        commit_date = commit_data['commit']['committer']['date'][:10]  # YYYY-MM-DD

        # Skip if already in registry
        if commit_hash in existing_commits:
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

    if new_models_added > 0:
        # Update last_updated timestamp
        registry['last_updated'] = datetime.now().strftime('%Y-%m-%d')

        # Save updated registry
        with open(REGISTRY_FILE, 'w') as f:
            json.dump(registry, f, indent=2)

        print(f"✅ Added {new_models_added} new model(s) to registry")
        print(f"📄 Registry updated: {REGISTRY_FILE}")
    else:
        print("✅ Registry is up to date - no new models found")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Download openpilot models from GitHub (separated driving/DM models)'
    )
    parser.add_argument('action', choices=['list', 'download', 'check-updates', 'add-model', 'update-registry'],
                       help='Action to perform')
    parser.add_argument('--type', choices=['driving', 'dm'],
                       help='Model type: driving or dm (driver monitoring)')
    parser.add_argument('model_id', nargs='?',
                       help='Model ID to download or add')
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

    elif args.action == 'update-registry':
        return update_registry_from_github()


if __name__ == '__main__':
    import sys
    sys.exit(main())
