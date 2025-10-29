#!/usr/bin/env python3
"""
Model Swapper V3 - Separated Driving and Driver Monitoring Models
Handles two independent model types:
- Driving Models: driving_vision.onnx + driving_policy.onnx
- Driver Monitoring Models: dmonitoring_model.onnx
"""
import shutil
import json
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
    - Driving models: /data/models/ (vision + policy)
    - DM models: /data/dm-models/ (dmonitoring)
    """

    # Base paths
    BASE_DATA_DIR = Path('/data') if Path('/data').exists() else Path.home() / 'driving_data'
    ACTIVE_DIR = Path(__file__).parent / 'models'

    # Model type configurations
    MODEL_CONFIGS = {
        ModelType.DRIVING: {
            'models_dir': BASE_DATA_DIR / 'models',
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
            'active_file': '.active_driving_model',
            'display_name': 'Driving Model'
        },
        ModelType.DM: {
            'models_dir': BASE_DATA_DIR / 'dm-models',
            'onnx_files': [
                'dmonitoring_model.onnx',
            ],
            'pkl_files': [
                'dmonitoring_model_tinygrad.pkl',
            ],
            'active_file': '.active_dm_model',
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

        self.active_model_file = self.models_dir / self.config['active_file']

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
        Swap active model using ONNX + optional PKL caching

        Returns:
            dict with swap status and whether compilation is needed
        """
        # Resolve name to ID if needed
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

        # Check which PKL files are available (for caching)
        available_pkl = [f for f in self.pkl_files if (source_dir / f).exists()]

        # Backup current model
        self._backup_current_model()

        # Remove existing symlinks for this model type only
        self._remove_symlinks()

        # Create ONNX symlinks (always required)
        for filename in self.onnx_files:
            src = source_dir / filename
            dst = self.ACTIVE_DIR / filename
            dst.symlink_to(src)

        # Create PKL symlinks if cached (skip compilation)
        symlinked_pkl = []
        for filename in available_pkl:
            src = source_dir / filename
            dst = self.ACTIVE_DIR / filename
            dst.symlink_to(src)
            symlinked_pkl.append(filename)

        # Update active model tracker
        with open(self.active_model_file, 'w') as f:
            f.write(model_id)

        needs_compilation = len(symlinked_pkl) < len(self.pkl_files)

        return {
            'model_id': model_id,
            'model_type': self.model_type.value,
            'onnx_files': len(self.onnx_files),
            'cached_pkl_files': len(symlinked_pkl),
            'total_pkl_files': len(self.pkl_files),
            'needs_compilation': needs_compilation,
            'compilation_note': 'openpilot will compile ONNX→PKL on next boot' if needs_compilation else 'using cached PKL files'
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
            with open(self.active_model_file, 'r') as f:
                return f.read().strip()
        return 'unknown'

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
    parser.add_argument('action', choices=['list', 'list-simple', 'swap', 'verify', 'active', 'cache'],
                       help='Action to perform')
    parser.add_argument('model_id', nargs='?', help='Model ID for swap/verify/cache')

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

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
