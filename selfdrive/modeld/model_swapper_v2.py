#!/usr/bin/env python3
"""
Model Swapper V2 - ONNX-based with PKL caching
Swaps ONNX models and optionally symlinks pre-compiled PKL for speed
"""
import shutil
import json
from pathlib import Path


class ModelSwapper:
    """
    Swap driving models using ONNX with PKL caching

    Architecture:
    - Primary: ONNX files (portable, from GitHub)
    - Cache: PKL files (device-specific, compiled once)
    - Active: Symlinks in selfdrive/modeld/models/
    """

    # Use C3 path if available, otherwise local testing path
    MODELS_DIR = Path('/data/models') if Path('/data').exists() else Path.home() / 'driving_data' / 'models'
    ACTIVE_DIR = Path(__file__).parent / 'models'

    # Required ONNX files (portable, from GitHub)
    ONNX_FILES = [
        'driving_vision.onnx',
        'driving_policy.onnx',
        'dmonitoring_model.onnx',
    ]

    # Optional PKL files (compiled, cached for speed)
    PKL_FILES = [
        'driving_vision_tinygrad.pkl',
        'driving_policy_tinygrad.pkl',
        'driving_vision_metadata.pkl',
        'driving_policy_metadata.pkl',
        'dmonitoring_model_tinygrad.pkl',
    ]

    def __init__(self):
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.active_model_file = self.MODELS_DIR / '.active_model'

    def list_models(self):
        """List all available models with ONNX files"""
        models = []

        if not self.MODELS_DIR.exists():
            return models

        for model_dir in self.MODELS_DIR.iterdir():
            if model_dir.is_dir() and not model_dir.name.startswith('_'):
                info_file = model_dir / 'model_info.json'
                if info_file.exists():
                    try:
                        with open(info_file) as f:
                            info = json.load(f)

                        # Check if ONNX files exist
                        has_onnx = all((model_dir / f).exists() for f in self.ONNX_FILES)

                        # Check which PKL files exist (cached)
                        cached_pkl = sum(1 for f in self.PKL_FILES if (model_dir / f).exists())

                        models.append({
                            'id': model_dir.name,
                            'has_onnx': has_onnx,
                            'cached_pkl_count': cached_pkl,
                            **info
                        })
                    except Exception as e:
                        print(f"Warning: Could not load {info_file}: {e}")
                        continue

        return models

    def swap_model(self, model_id: str) -> dict:
        """
        Swap active model using ONNX + optional PKL caching

        Returns:
            dict with swap status and whether compilation is needed
        """
        source_dir = self.MODELS_DIR / model_id

        if not source_dir.exists():
            raise ValueError(f"Model '{model_id}' not found in {self.MODELS_DIR}")

        # Verify required ONNX files exist
        missing_onnx = []
        for filename in self.ONNX_FILES:
            if not (source_dir / filename).exists():
                missing_onnx.append(filename)

        if missing_onnx:
            raise ValueError(
                f"Model '{model_id}' is missing required ONNX files: {', '.join(missing_onnx)}"
            )

        # Check which PKL files are available (for caching)
        available_pkl = [f for f in self.PKL_FILES if (source_dir / f).exists()]

        # Backup current model
        self._backup_current_model()

        # Remove existing symlinks
        self._remove_symlinks()

        # Create ONNX symlinks (always required)
        for filename in self.ONNX_FILES:
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

        needs_compilation = len(symlinked_pkl) < len(self.PKL_FILES)

        return {
            'model_id': model_id,
            'onnx_files': len(self.ONNX_FILES),
            'cached_pkl_files': len(symlinked_pkl),
            'needs_compilation': needs_compilation,
            'compilation_note': 'openpilot will compile ONNX→PKL on next boot' if needs_compilation else 'using cached PKL files'
        }

    def _remove_symlinks(self):
        """Remove existing symlinks in active directory"""
        all_files = self.ONNX_FILES + self.PKL_FILES

        for filename in all_files:
            filepath = self.ACTIVE_DIR / filename
            if filepath.is_symlink():
                filepath.unlink()
            elif filepath.exists() and not filepath.is_symlink():
                # Real file, not symlink - back it up before removing
                backup_dir = self.MODELS_DIR / '_backup_replaced'
                backup_dir.mkdir(exist_ok=True)
                shutil.copy2(filepath, backup_dir / filename)
                filepath.unlink()

    def cache_compiled_pkl(self, model_id: str):
        """
        After openpilot compiles ONNX→PKL, cache the PKL files to model storage

        This should be called after first boot with a new model
        """
        source_dir = self.MODELS_DIR / model_id

        if not source_dir.exists():
            raise ValueError(f"Model directory not found: {source_dir}")

        cached_count = 0
        for filename in self.PKL_FILES:
            active_file = self.ACTIVE_DIR / filename
            cached_file = source_dir / filename

            # If PKL exists in active dir but not in cache, copy it
            if active_file.exists() and not active_file.is_symlink() and not cached_file.exists():
                shutil.copy2(active_file, cached_file)
                cached_count += 1

        return cached_count

    def _backup_current_model(self):
        """Backup currently active model to /data/models/_backup_current/"""
        backup_dir = self.MODELS_DIR / '_backup_current'
        backup_dir.mkdir(exist_ok=True)

        all_files = self.ONNX_FILES + self.PKL_FILES

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
        source_dir = self.MODELS_DIR / model_id

        if not source_dir.exists():
            return {
                'valid': False,
                'error': f"Model directory not found: {source_dir}"
            }

        # Check ONNX files (required)
        missing_onnx = []
        onnx_sizes = {}
        for filename in self.ONNX_FILES:
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
        for filename in self.PKL_FILES:
            filepath = source_dir / filename
            if filepath.exists():
                pkl_sizes[filename] = filepath.stat().st_size

        return {
            'valid': True,
            'onnx_files': onnx_sizes,
            'cached_pkl_files': pkl_sizes,
            'compilation_needed': len(pkl_sizes) < len(self.PKL_FILES)
        }


def main():
    """CLI tool for model management"""
    import argparse

    parser = argparse.ArgumentParser(description='Openpilot Model Swapper V2 (ONNX-based)')
    parser.add_argument('action', choices=['list', 'swap', 'verify', 'active', 'cache'],
                       help='Action to perform')
    parser.add_argument('model_id', nargs='?', help='Model ID for swap/verify/cache')

    args = parser.parse_args()
    swapper = ModelSwapper()

    if args.action == 'list':
        models = swapper.list_models()
        if not models:
            print("No models found in /data/models/")
            print("\nTo add a model:")
            print("  1. Create /data/models/{model_name}/")
            print("  2. Download ONNX files from GitHub")
            print("  3. Create model_info.json")
        else:
            print(f"Available models ({len(models)}):\n")
            for model in models:
                status = "✓ ONNX" if model['has_onnx'] else "✗ Missing ONNX"
                cache_info = f"({model['cached_pkl_count']}/5 PKL cached)" if model['cached_pkl_count'] > 0 else "(no cache)"

                print(f"  {model['id']}")
                print(f"    Name: {model.get('name', 'Unknown')}")
                print(f"    Version: {model.get('version', 'Unknown')}")
                print(f"    Status: {status} {cache_info}")
                print()

    elif args.action == 'active':
        active = swapper.get_active_model()
        print(f"Active model: {active}")

    elif args.action == 'swap':
        if not args.model_id:
            print("Error: model_id required for swap")
            return 1

        try:
            result = swapper.swap_model(args.model_id)
            print(f"✅ Successfully swapped to model: {args.model_id}")
            print(f"   ONNX files: {result['onnx_files']}")
            print(f"   Cached PKL: {result['cached_pkl_files']}/{len(swapper.PKL_FILES)}")
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
            print("Error: model_id required for verify")
            return 1

        result = swapper.verify_model(args.model_id)
        if result['valid']:
            print(f"✅ Model '{args.model_id}' is valid")
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
            print(f"❌ Model '{args.model_id}' is invalid")
            print(f"Error: {result['error']}")
            return 1

    elif args.action == 'cache':
        if not args.model_id:
            print("Error: model_id required for cache")
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
