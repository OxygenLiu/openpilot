"""Tests for plugin manifest parsing and compatibility checking."""
import json
import os
import tempfile

import pytest
from openpilot.selfdrive.plugins.manifest import load_manifest, check_compatibility, parse_version


class TestParseVersion:
  def test_standard(self):
    assert parse_version('0.10.3') == (0, 10, 3)

  def test_major_only(self):
    assert parse_version('1') == (1,)

  def test_invalid(self):
    assert parse_version('abc') == (0, 0, 0)

  def test_none(self):
    assert parse_version(None) == (0, 0, 0)


class TestLoadManifest:
  def _write_manifest(self, tmpdir, data):
    path = os.path.join(tmpdir, 'plugin.json')
    with open(path, 'w') as f:
      json.dump(data, f)
    return tmpdir

  def test_valid_manifest(self, tmp_path):
    data = {
      'id': 'test-plugin',
      'name': 'Test Plugin',
      'version': '1.0.0',
      'type': 'hook',
    }
    self._write_manifest(str(tmp_path), data)
    manifest = load_manifest(str(tmp_path))
    assert manifest is not None
    assert manifest['id'] == 'test-plugin'

  def test_missing_required_field(self, tmp_path):
    data = {
      'id': 'test-plugin',
      'name': 'Test Plugin',
      # missing 'version' and 'type'
    }
    self._write_manifest(str(tmp_path), data)
    manifest = load_manifest(str(tmp_path))
    assert manifest is None

  def test_invalid_type(self, tmp_path):
    data = {
      'id': 'test-plugin',
      'name': 'Test Plugin',
      'version': '1.0.0',
      'type': 'invalid',
    }
    self._write_manifest(str(tmp_path), data)
    manifest = load_manifest(str(tmp_path))
    assert manifest is None

  def test_no_plugin_json(self, tmp_path):
    manifest = load_manifest(str(tmp_path))
    assert manifest is None

  def test_invalid_json(self, tmp_path):
    path = os.path.join(str(tmp_path), 'plugin.json')
    with open(path, 'w') as f:
      f.write('{invalid json')
    manifest = load_manifest(str(tmp_path))
    assert manifest is None


class TestCheckCompatibility:
  def test_compatible(self):
    manifest = {'id': 'test', 'min_openpilot': '0.10.0', 'max_openpilot': '0.10.99'}
    assert check_compatibility(manifest)

  def test_too_new(self):
    manifest = {'id': 'test', 'min_openpilot': '0.11.0'}
    assert not check_compatibility(manifest)

  def test_too_old(self):
    manifest = {'id': 'test', 'max_openpilot': '0.9.0'}
    assert not check_compatibility(manifest)

  def test_no_version_constraints(self):
    manifest = {'id': 'test'}
    assert check_compatibility(manifest)
