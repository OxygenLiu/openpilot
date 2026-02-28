#!/usr/bin/env python3
"""
Model Swapper V3 - Separated Driving and Driver Monitoring Models
Handles two independent model types:
- Driving Models: driving_vision.onnx + driving_policy.onnx
- Driver Monitoring Models: dmonitoring_model.onnx
"""
import shutil
import json
import subprocess
from pathlib import Path
from enum import Enum


class ModelType(Enum):
    """Model type enumeration"""
    DRIVING = "driving"
    DM = "dm"


class ModelSwapper:
    """
    Swap driving or DM models using ONNX with PKL caching

    Architecture:
    - Primary: ONNX files (portable, from GitHub)
    - Cache: PKL files (device-specific, compiled once)
    - Active: Symlinks in selfdrive/modeld/models/

    Two independent model systems:
    - Driving models: /data/models/driving/ (vision + policy)
    - DM models: /data/models/dm/ (dmonitoring)
    """

    # Base paths
    BASE_DATA_DIR = Path('/data') if Path('/data').exists() else Path.home() / 'driving_data'
    OPENPILOT_DIR = Path('/data/openpilot') if Path('/data/openpilot').exists() else Path.home() / 'openpilot'
    ACTIVE_DIR = OPENPILOT_DIR / 'selfdrive' / 'modeld' / 'models'

    @staticmethod
    def get_tinygrad_commit() -> str:
        """Get current tinygrad_repo commit hash (short)."""
        tinygrad_dir = ModelSwapper.OPENPILOT_DIR / 'tinygrad_repo'
        try:
            result = subprocess.run(
                ['git', '-C', str(tinygrad_dir), 'rev-parse', '--short', 'HEAD'],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ''
        except Exception:
            return ''

    # Model type configurations
    MODEL_CONFIGS = {
        ModelType.DRIVING: {
            'models_dir': BASE_DATA_DIR / 'models' / 'driving',
            'onnx_files': [
                'driving_vision.onnx',
                'driving_policy.onnx',
            ],
            'pkl_files': [
                'driving_vision_tinygrad.pkl',
                'driving_policy_tinygrad.pkl',
                'driving_vision_metadata.pkl',
                'driving_policy_metadata.pkl',
            ],
            'active_file': 'active_driving_model',
            'display_name': 'Driving Model'
        },
        ModelType.DM: {
            'models_dir': BASE_DATA_DIR / 'models' / 'dm',
            'onnx_files': [
                'dmonitoring_model.onnx',
            ],
            'pkl_files': [
                'dmonitoring_model_tinygrad.pkl',
            ],
            'active_file': 'active_dm_model',
            'display_name': 'Driver Monitoring Model'
        }
    }

    def __init__(self, model_type: ModelType):
        """
        Initialize swapper for specific model type

        Args:
            model_type: ModelType.DRIVING or ModelType.DM
        """
        self.model_type = model_type
        self.config = self.MODEL_CONFIGS[model_type]

        # Setup directories
        self.models_dir = self.config['models_dir']
        self.models_dir.mkdir(parents=True, exist_ok=True)

        # Active file is at parent level: /data/models/active_*
        self.active_model_file = self.models_dir.parent / self.config['active_file']

        self.onnx_files = self.config['onnx_files']
        self.pkl_files = self.config['pkl_files']
        self.display_name = self.config['display_name']

    def list_models(self):
        """List all available models for this type"""
        models = []

        if not self.models_dir.exists():
            return models

        for model_dir in self.models_dir.iterdir():
            if model_dir.is_dir() and not model_dir.name.startswith('_'):
                info_file = model_dir / 'model_info.json'
                if info_file.exists():
                    try:
                        with open(info_file) as f:
                            info = json.load(f)

                        # Check if ONNX files exist
                        has_onnx = all((model_dir / f).exists() for f in self.onnx_files)

                        # Check which PKL files exist (cached)
                        cached_pkl = sum(1 for f in self.pkl_files if (model_dir / f).exists())

                        models.append({
                            'id': model_dir.name,
                            'has_onnx': has_onnx,
                            'cached_pkl_count': cached_pkl,
                            'total_pkl_count': len(self.pkl_files),
                            **info
                        })
                    except Exception as e:
                        print(f"Warning: Could not load {info_file}: {e}")
                        continue

        # Sort by date in descending order (newest first)
        # Use '0000-00-00' as fallback for models without date field
        models.sort(key=lambda m: m.get('date', '0000-00-00'), reverse=True)

        return models

    def resolve_model_id(self, name_or_id: str) -> str:
        """
        Resolve a model name or ID to the actual model ID
        Allows UI to use display names while backend uses IDs
        """
        # First check if it's already a valid ID (directory exists)
        if (self.models_dir / name_or_id).exists():
            return name_or_id

        # Try to match by name
        models = self.list_models()
        for model in models:
            if model.get('name') == name_or_id:
                return model['id']

        # No match found, return original (will error in swap_model)
        return name_or_id

    def swap_model(self, model_id: str) -> dict:
        """
        Swap active model immediately (git-safe architecture)

        Process:
        1. Cache current model's compiled PKL files to /data storage
        2. Validate new model's ONNX files exist in /data
        3. Copy new model's ONNX and PKL (if available) to selfdrive/modeld/models/
        4. Update .active_* tracker file

        After reboot, if PKL files are missing, openpilot auto-compiles ONNX→PKL.
        Next swap will cache those compiled PKL files.

        Returns:
            dict with swap status and whether compilation is needed
        """
        # STEP 1: Cache compiled PKL files from CURRENT model (if any)
        current_model_id = self.get_active_model()
        cached_count = 0

        if current_model_id and current_model_id != 'unknown':
            try:
                cached_count = self.cache_compiled_pkl(current_model_id)
            except Exception as e:
                # Don't fail swap if caching fails, just log it
                pass

        # STEP 2: Validate NEW model ONNX files exist in /data storage
        model_id = self.resolve_model_id(model_id)
        source_dir = self.models_dir / model_id

        if not source_dir.exists():
            raise ValueError(f"Model '{model_id}' not found in {self.models_dir}")

        # Verify required ONNX files exist
        missing_onnx = []
        for filename in self.onnx_files:
            if not (source_dir / filename).exists():
                missing_onnx.append(filename)

        if missing_onnx:
            raise ValueError(
                f"Model '{model_id}' is missing required ONNX files: {', '.join(missing_onnx)}"
            )

        # Check tinygrad compatibility for cached PKL files
        current_tg = self.get_tinygrad_commit()
        cached_tg = ''
        tg_file = source_dir / '.tinygrad_commit'
        if tg_file.exists():
            cached_tg = tg_file.read_text().strip()

        pkl_compatible = bool(current_tg and cached_tg and current_tg == cached_tg)

        # Only use cached PKL if tinygrad version matches
        available_pkl = []
        if pkl_compatible:
            available_pkl = [f for f in self.pkl_files if (source_dir / f).exists()]

        # STEP 3: Copy ONNX and PKL files to selfdrive/modeld/models/
        copied_files = []

        # Copy ONNX files (always required)
        for filename in self.onnx_files:
            src = source_dir / filename
            dst = self.ACTIVE_DIR / filename
            if dst.exists() or dst.is_symlink():
                dst.unlink()
            shutil.copy2(src, dst)
            copied_files.append(filename)

        # Remove stale PKL files from runtime dir (force recompile if incompatible)
        for filename in self.pkl_files:
            dst = self.ACTIVE_DIR / filename
            if dst.exists() or dst.is_symlink():
                dst.unlink()

        # Copy compatible PKL files if available
        for filename in available_pkl:
            src = source_dir / filename
            dst = self.ACTIVE_DIR / filename
            shutil.copy2(src, dst)
            copied_files.append(filename)

        # STEP 4: Update active model tracker (JSON: id + name for fast UI reads)
        info = None
        info_file = source_dir / 'model_info.json'
        if info_file.exists():
            try:
                with open(info_file) as f:
                    info = json.load(f)
            except Exception:
                pass
        active_data = {
            'id': model_id,
            'name': info.get('name', model_id) if info else model_id,
            'tinygrad': current_tg,
        }
        with open(self.active_model_file, 'w') as f:
            json.dump(active_data, f)

        needs_compilation = len(available_pkl) < len(self.pkl_files)

        return {
            'model_id': model_id,
            'model_type': self.model_type.value,
            'copied_files': copied_files,
            'onnx_files': len(self.onnx_files),
            'cached_pkl_files': len(available_pkl),
            'total_pkl_files': len(self.pkl_files),
            'needs_compilation': needs_compilation,
            'compilation_note': (
                f'PKL cache built by tinygrad {cached_tg}, current is {current_tg} — will recompile'
                if cached_tg and not pkl_compatible
                else 'openpilot will compile ONNX→PKL on first boot' if needs_compilation
                else 'using cached PKL files'
            ),
            'tinygrad_commit': current_tg,
            'pkl_tinygrad': cached_tg,
            'pkl_compatible': pkl_compatible,
            'requires_reboot': True,
            'reboot_note': 'Reboot required to activate new model',
            'previous_model_cached': cached_count
        }

    def _remove_symlinks(self):
        """Remove existing symlinks for this model type only"""
        all_files = self.onnx_files + self.pkl_files

        for filename in all_files:
            filepath = self.ACTIVE_DIR / filename
            if filepath.is_symlink():
                filepath.unlink()
            elif filepath.exists() and not filepath.is_symlink():
                # Real file, not symlink - back it up before removing
                backup_dir = self.models_dir / '_backup_replaced'
                backup_dir.mkdir(exist_ok=True)
                shutil.copy2(filepath, backup_dir / filename)
                filepath.unlink()

    def cache_compiled_pkl(self, model_id: str):
        """
        After openpilot compiles ONNX→PKL, cache the PKL files to model storage

        This should be called after first boot with a new model
        """
        source_dir = self.models_dir / model_id

        if not source_dir.exists():
            raise ValueError(f"Model directory not found: {source_dir}")

        cached_count = 0
        for filename in self.pkl_files:
            active_file = self.ACTIVE_DIR / filename
            cached_file = source_dir / filename

            # If PKL exists in active dir but not in cache, copy it
            if active_file.exists() and not active_file.is_symlink() and not cached_file.exists():
                shutil.copy2(active_file, cached_file)
                cached_count += 1

        # Record which tinygrad built these PKL files
        if cached_count > 0:
            tg_commit = self.get_tinygrad_commit()
            if tg_commit:
                (source_dir / '.tinygrad_commit').write_text(tg_commit)

        return cached_count

    def _backup_current_model(self):
        """Backup currently active model"""
        backup_dir = self.models_dir / '_backup_current'
        backup_dir.mkdir(exist_ok=True)

        all_files = self.onnx_files + self.pkl_files

        for filename in all_files:
            src = self.ACTIVE_DIR / filename
            if src.exists():
                # Follow symlink if necessary
                if src.is_symlink():
                    real_src = src.resolve()
                    if real_src.exists():
                        shutil.copy2(real_src, backup_dir / filename)
                else:
                    shutil.copy2(src, backup_dir / filename)

    def get_active_model(self) -> str:
        """Get currently active model ID from file tracker"""
        if self.active_model_file.exists():
            raw = self.active_model_file.read_text().strip()
            try:
                data = json.loads(raw)
                return data.get('id', raw)
            except (json.JSONDecodeError, AttributeError):
                return raw
        return 'unknown'

    def delete_model(self, model_id: str) -> dict:
        """
        Delete a model from storage

        Returns:
            dict with deletion status
        """
        # Resolve name to ID if needed
        model_id = self.resolve_model_id(model_id)

        # Check if model exists
        model_dir = self.models_dir / model_id
        if not model_dir.exists():
            raise ValueError(f"Model '{model_id}' not found in {self.models_dir}")

        # Prevent deletion of active model
        active_model = self.get_active_model()
        if model_id == active_model:
            raise ValueError(f"Cannot delete active model '{model_id}'. Please switch to a different model first.")

        # Delete the model directory
        shutil.rmtree(model_dir)

        return {
            'model_id': model_id,
            'model_type': self.model_type.value,
            'deleted': True
        }

    def verify_model(self, model_id: str) -> dict:
        """
        Verify a model has required ONNX files and check PKL cache

        Returns:
            Dict with verification results
        """
        source_dir = self.models_dir / model_id

        if not source_dir.exists():
            return {
                'valid': False,
                'error': f"Model directory not found: {source_dir}"
            }

        # Check ONNX files (required)
        missing_onnx = []
        onnx_sizes = {}
        for filename in self.onnx_files:
            filepath = source_dir / filename
            if not filepath.exists():
                missing_onnx.append(filename)
            else:
                onnx_sizes[filename] = filepath.stat().st_size

        if missing_onnx:
            return {
                'valid': False,
                'error': f"Missing required ONNX files: {', '.join(missing_onnx)}",
                'onnx_files': onnx_sizes
            }

        # Check PKL files (optional, for caching info)
        pkl_sizes = {}
        for filename in self.pkl_files:
            filepath = source_dir / filename
            if filepath.exists():
                pkl_sizes[filename] = filepath.stat().st_size

        return {
            'valid': True,
            'onnx_files': onnx_sizes,
            'cached_pkl_files': pkl_sizes,
            'compilation_needed': len(pkl_sizes) < len(self.pkl_files)
        }


def main():
    """CLI tool for model management"""
    import argparse

    parser = argparse.ArgumentParser(description='Openpilot Model Swapper V3 (Separated Driving/DM)')
    parser.add_argument('--type', choices=['driving', 'dm'], required=True,
                       help='Model type: driving or dm (driver monitoring)')
    parser.add_argument('action', choices=['list', 'list-simple', 'list-with-dates', 'swap', 'verify', 'active', 'cache', 'delete'],
                       help='Action to perform')
    parser.add_argument('model_id', nargs='?', help='Model ID for swap/verify/cache/delete')

    args = parser.parse_args()

    # Create swapper for specified type
    model_type = ModelType.DRIVING if args.type == 'driving' else ModelType.DM
    swapper = ModelSwapper(model_type)

    if args.action == 'list':
        models = swapper.list_models()
        if not models:
            print(f"No {swapper.display_name}s found in {swapper.models_dir}/")
            print(f"\nTo add a {swapper.display_name.lower()}:")
            print(f"  1. Create {swapper.models_dir}/{{model_name}}/")
            print(f"  2. Download ONNX files from GitHub")
            print(f"  3. Create model_info.json")
        else:
            print(f"Available {swapper.display_name}s ({len(models)}):\n")
            for model in models:
                status = "✓ ONNX" if model['has_onnx'] else "✗ Missing ONNX"
                cache_info = f"({model['cached_pkl_count']}/{model['total_pkl_count']} PKL cached)" if model['cached_pkl_count'] > 0 else "(no cache)"

                print(f"  {model['id']}")
                print(f"    Name: {model.get('name', 'Unknown')}")
                print(f"    Version: {model.get('version', 'Unknown')}")
                print(f"    Status: {status} {cache_info}")
                print()

    elif args.action == 'list-simple':
        # Simple list for UI dialogs - just model names, one per line
        models = swapper.list_models()
        for model in models:
            # Use name if available, otherwise use ID
            display_name = model.get('name') or model['id']
            print(display_name)

    elif args.action == 'list-with-dates':
        # List with dates for UI dialogs - format: "Name (YYYY-MM-DD)"
        models = swapper.list_models()
        for model in models:
            # Use name if available, otherwise use ID
            display_name = model.get('name') or model['id']
            # Add date in parentheses if available
            if 'date' in model:
                print(f"{display_name} ({model['date']})")
            else:
                print(display_name)

    elif args.action == 'active':
        active = swapper.get_active_model()
        # Get the model info to return the display name instead of just ID
        if active and active != f"No active {swapper.display_name.lower()}":
            models = swapper.list_models()
            for model in models:
                if model['id'] == active:
                    # Return name if available, otherwise ID
                    print(model.get('name') or active)
                    break
            else:
                # Model not found in list, just return ID
                print(active)
        else:
            print(active)

    elif args.action == 'swap':
        if not args.model_id:
            print(f"Error: model_id required for swap")
            return 1

        try:
            result = swapper.swap_model(args.model_id)
            print(f"✅ Successfully swapped to {swapper.display_name.lower()}: {args.model_id}")
            print(f"   ONNX files: {result['onnx_files']}")
            print(f"   Cached PKL: {result['cached_pkl_files']}/{result['total_pkl_files']}")
            if result['needs_compilation']:
                print(f"   ⏳ {result['compilation_note']}")
            else:
                print(f"   ⚡ Using cached PKL - no compilation needed!")
            print("⚠️  Restart openpilot for changes to take effect")
        except Exception as e:
            print(f"❌ Swap failed: {e}")
            return 1

    elif args.action == 'verify':
        if not args.model_id:
            print(f"Error: model_id required for verify")
            return 1

        result = swapper.verify_model(args.model_id)
        if result['valid']:
            print(f"✅ {swapper.display_name} '{args.model_id}' is valid")
            print("\nONNX files (required):")
            for filename, size in result['onnx_files'].items():
                print(f"  ✓ {filename}: {size / 1024 / 1024:.1f} MB")

            if result['cached_pkl_files']:
                print("\nCached PKL files (compiled):")
                for filename, size in result['cached_pkl_files'].items():
                    print(f"  ✓ {filename}: {size / 1024 / 1024:.1f} MB")

            if result['compilation_needed']:
                print("\n⏳ Some PKL files not cached - will compile on first use")
            else:
                print("\n⚡ All PKL files cached - instant swap!")
        else:
            print(f"❌ {swapper.display_name} '{args.model_id}' is invalid")
            print(f"Error: {result['error']}")
            return 1

    elif args.action == 'cache':
        if not args.model_id:
            print(f"Error: model_id required for cache")
            return 1

        try:
            count = swapper.cache_compiled_pkl(args.model_id)
            print(f"✅ Cached {count} PKL files to {args.model_id}")
        except Exception as e:
            print(f"❌ Cache failed: {e}")
            return 1

    elif args.action == 'delete':
        if not args.model_id:
            print(f"Error: model_id required for delete")
            return 1

        try:
            result = swapper.delete_model(args.model_id)
            print(f"✅ Successfully deleted {swapper.display_name.lower()}: {args.model_id}")
        except Exception as e:
            print(f"❌ Delete failed: {e}")
            return 1

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
