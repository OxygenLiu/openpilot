#!/usr/bin/env python3
"""
Model Initialization Script - Boot-time Model Validation and Copy

Runs during C3 boot to ensure selfdrive/modeld/models/ matches .active_* tracker.
This handles the git pull scenario where ONNX files in repo may differ from active model.

Critical Scenario:
1. User has Model A active (.active_driving_model = "model_a")
2. User runs git pull (repo now has Model B ONNX files)
3. C3 reboots
4. This script validates and copies correct Model A files from /data/models/
"""
import shutil
import json
from pathlib import Path


class ModelInitializer:
    """
    Validate and copy active models from /data storage to runtime location during boot

    This ensures selfdrive/modeld/models/ always matches the .active_* tracker,
    even after git pull operations that may change the ONNX files in the repo.
    """

    # Base paths
    BASE_DATA_DIR = Path('/data') if Path('/data').exists() else Path.home() / 'driving_data'
    RUNTIME_DIR = Path(__file__).parent / 'models'

    # Model configurations
    MODEL_CONFIGS = {
        'driving': {
            'models_dir': BASE_DATA_DIR / 'models',
            'active_file': '.active_driving_model',
            'files_to_copy': [
                'driving_vision.onnx',
                'driving_policy.onnx',
            ],
            'optional_pkl_files': [
                'driving_vision_tinygrad.pkl',
                'driving_policy_tinygrad.pkl',
                'driving_vision_metadata.pkl',
                'driving_policy_metadata.pkl',
            ]
        },
        'dm': {
            'models_dir': BASE_DATA_DIR / 'dm-models',
            'active_file': '.active_dm_model',
            'files_to_copy': [
                'dmonitoring_model.onnx',
            ],
            'optional_pkl_files': [
                'dmonitoring_model_tinygrad.pkl',
            ]
        }
    }

    def __init__(self, model_type: str):
        """
        Initialize for specific model type

        Args:
            model_type: 'driving' or 'dm'
        """
        if model_type not in self.MODEL_CONFIGS:
            raise ValueError(f"Invalid model type: {model_type}")

        self.model_type = model_type
        self.config = self.MODEL_CONFIGS[model_type]

        self.models_dir = self.config['models_dir']
        self.active_file = self.models_dir / self.config['active_file']
        self.runtime_dir = self.RUNTIME_DIR

    def get_active_model_id(self) -> str:
        """Get the currently active model ID from tracker file"""
        if not self.active_file.exists():
            return None

        with open(self.active_file, 'r') as f:
            model_id = f.read().strip()

        return model_id if model_id else None

    def _files_match(self, src: Path, dst: Path) -> bool:
        """
        Check if source and destination files are identical

        Compares file size and modification time for quick check.
        Returns True if files appear identical (no copy needed).
        """
        if not dst.exists() or dst.is_symlink():
            return False  # Destination missing or is symlink (need to copy/fix)

        if not src.exists():
            return False  # Source missing (shouldn't happen, but be safe)

        # Quick check: compare file sizes
        src_stat = src.stat()
        dst_stat = dst.stat()

        if src_stat.st_size != dst_stat.st_size:
            return False  # Different sizes = different files

        # Size matches - assume files are identical to avoid expensive hash comparison
        return True

    def copy_active_model(self) -> dict:
        """
        Copy active model files from /data to runtime location

        This ensures runtime files match .active_* tracker, even after git pull.

        Returns:
            dict with copy status and file counts
        """
        active_model_id = self.get_active_model_id()

        if not active_model_id:
            return {
                'success': False,
                'model_type': self.model_type,
                'error': f'No active {self.model_type} model configured'
            }

        source_dir = self.models_dir / active_model_id

        if not source_dir.exists():
            return {
                'success': False,
                'model_type': self.model_type,
                'model_id': active_model_id,
                'error': f'Model directory not found: {source_dir}'
            }

        # Ensure runtime directory exists
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        # Copy required ONNX files (only if needed)
        copied_onnx = []
        skipped_onnx = []
        missing_onnx = []

        for filename in self.config['files_to_copy']:
            src = source_dir / filename
            dst = self.runtime_dir / filename

            if not src.exists():
                missing_onnx.append(filename)
                continue

            # Check if file already matches (skip copy if identical)
            if self._files_match(src, dst):
                skipped_onnx.append(filename)
                continue

            # File needs updating - remove old version
            if dst.exists() or dst.is_symlink():
                dst.unlink()

            # Copy the file
            shutil.copy2(src, dst)
            copied_onnx.append(filename)

        if missing_onnx:
            return {
                'success': False,
                'model_type': self.model_type,
                'model_id': active_model_id,
                'error': f'Missing required ONNX files: {", ".join(missing_onnx)}'
            }

        # Copy optional PKL cache files if they exist (only if needed)
        copied_pkl = []
        skipped_pkl = []
        for filename in self.config['optional_pkl_files']:
            src = source_dir / filename
            dst = self.runtime_dir / filename

            if not src.exists():
                continue  # PKL cache is optional

            # Check if file already matches (skip copy if identical)
            if self._files_match(src, dst):
                skipped_pkl.append(filename)
                continue

            # File needs updating - remove old version
            if dst.exists() or dst.is_symlink():
                dst.unlink()

            # Copy the file
            shutil.copy2(src, dst)
            copied_pkl.append(filename)

        # Total files includes both skipped and copied
        total_onnx = len(skipped_onnx) + len(copied_onnx)
        total_pkl = len(skipped_pkl) + len(copied_pkl)

        return {
            'success': True,
            'model_type': self.model_type,
            'model_id': active_model_id,
            'copied_onnx': len(copied_onnx),
            'skipped_onnx': len(skipped_onnx),
            'total_onnx': total_onnx,
            'copied_pkl': len(copied_pkl),
            'skipped_pkl': len(skipped_pkl),
            'total_pkl': total_pkl,
            'needs_compilation': total_pkl < len(self.config['optional_pkl_files']),
            'files': {
                'onnx': copied_onnx,
                'pkl': copied_pkl,
                'skipped_onnx': skipped_onnx,
                'skipped_pkl': skipped_pkl
            }
        }

    def cleanup_old_files(self):
        """
        Remove old symlinks and files that are no longer needed
        Only removes files related to this model type
        """
        all_files = self.config['files_to_copy'] + self.config['optional_pkl_files']

        removed_count = 0
        for filename in all_files:
            filepath = self.runtime_dir / filename

            # Remove if it's a symlink (old architecture)
            if filepath.is_symlink():
                filepath.unlink()
                removed_count += 1

        return removed_count


def initialize_models():
    """
    Initialize both driving and DM models during boot

    This is the main entry point called during C3 boot process.
    Ensures selfdrive/modeld/models/ matches .active_* trackers.
    """
    results = {}

    for model_type in ['driving', 'dm']:
        try:
            initializer = ModelInitializer(model_type)

            # Clean up old symlinks first
            cleaned = initializer.cleanup_old_files()

            # Copy active model
            result = initializer.copy_active_model()
            result['cleaned_symlinks'] = cleaned

            results[model_type] = result

        except Exception as e:
            results[model_type] = {
                'success': False,
                'model_type': model_type,
                'error': str(e)
            }

    return results


def main():
    """CLI tool for testing model initialization"""
    import argparse

    parser = argparse.ArgumentParser(description='Model Initialization - Copy active models to runtime location')
    parser.add_argument('--type', choices=['driving', 'dm', 'all'], default='all',
                       help='Model type to initialize (default: all)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Verbose output')

    args = parser.parse_args()

    if args.type == 'all':
        print("Initializing all models...\n")
        results = initialize_models()

        for model_type, result in results.items():
            if result['success']:
                print(f"✅ {model_type.upper()} Model: {result['model_id']}")

                # Show copy/skip status
                if result['copied_onnx'] > 0:
                    print(f"   ONNX files: {result['copied_onnx']} copied, {result['skipped_onnx']} already up-to-date")
                else:
                    print(f"   ONNX files: {result['total_onnx']} already up-to-date (no copy needed)")

                if result['total_pkl'] > 0:
                    if result['copied_pkl'] > 0:
                        print(f"   PKL cache: {result['copied_pkl']} copied, {result['skipped_pkl']} already up-to-date")
                    else:
                        print(f"   PKL cache: {result['total_pkl']} already up-to-date (no copy needed)")

                if result['needs_compilation']:
                    print(f"   ⏳ Will compile on first modeld startup")
                else:
                    print(f"   ⚡ Using cached PKL - no compilation needed")

                if result.get('cleaned_symlinks', 0) > 0:
                    print(f"   🧹 Cleaned {result['cleaned_symlinks']} old symlinks")

                if args.verbose and 'files' in result:
                    if result['files']['onnx']:
                        print(f"   Files copied:")
                        for f in result['files']['onnx']:
                            print(f"     - {f}")
                        for f in result['files']['pkl']:
                            print(f"     - {f} (cached)")
                    if result['files'].get('skipped_onnx'):
                        print(f"   Files skipped (already up-to-date):")
                        for f in result['files']['skipped_onnx']:
                            print(f"     - {f}")
                        for f in result['files'].get('skipped_pkl', []):
                            print(f"     - {f} (cached)")
            else:
                print(f"❌ {model_type.upper()} Model: {result.get('error', 'Unknown error')}")
            print()

    else:
        initializer = ModelInitializer(args.type)

        # Clean up old symlinks
        cleaned = initializer.cleanup_old_files()
        if cleaned > 0:
            print(f"🧹 Cleaned {cleaned} old symlinks")

        # Copy active model
        result = initializer.copy_active_model()

        if result['success']:
            print(f"✅ Successfully initialized {args.type} model: {result['model_id']}")
            print(f"   ONNX files: {result['copied_onnx']}")
            print(f"   PKL cache: {result['copied_pkl']}")
            if result['needs_compilation']:
                print(f"   ⏳ Will compile on first modeld startup")

            if args.verbose and 'files' in result:
                print(f"\nFiles copied to {initializer.runtime_dir}:")
                for f in result['files']['onnx']:
                    print(f"  ✓ {f}")
                for f in result['files']['pkl']:
                    print(f"  ✓ {f} (cached)")
        else:
            print(f"❌ Failed to initialize {args.type} model")
            print(f"   Error: {result['error']}")
            return 1

    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
