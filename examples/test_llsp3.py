#!/usr/bin/env python3
"""Tests for spike3.llsp3 module — LLSP3 parsing, building, and compilation.

Run with: python -m pytest examples/test_llsp3.py -v
"""
import io
import json
import os
import tempfile
import zipfile

import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from spike3.llsp3 import (
    LLSP3File, parse_llsp3,
    build_python_llsp3, build_scratch_llsp3,
    BlockBuilder, scratch_blocks_to_python,
    _nanoid,
)


# ── Helpers ────────────────────────────────────────────────────────

def make_python_llsp3(source="import hub\nprint('hi')", slot=0,
                      hardware="gecko-atlantis") -> bytes:
    """Build a minimal Python-mode .llsp3 in memory."""
    manifest = {
        "type": "llsp3",
        "appType": "python",
        "name": "test_py",
        "slotIndex": slot,
        "hardware": {"id": hardware, "type": hardware},
        "extensions": [],
        "version": "3.0.0",
    }
    project_body = {"main": source}

    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as z:
        z.writestr("project.json", json.dumps(project_body))

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("scratch.sb3", inner_buf.getvalue())

    return outer_buf.getvalue()


def make_scratch_llsp3(blocks=None, slot=0) -> bytes:
    """Build a minimal Scratch-mode .llsp3 in memory."""
    if blocks is None:
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1",
                "parent": None,
                "inputs": {},
                "fields": {},
                "shadow": False,
                "topLevel": True,
                "x": 50, "y": 50,
            },
            "b1": {
                "opcode": "flipperdisplay_displayClear",
                "next": None,
                "parent": "hat1",
                "inputs": {},
                "fields": {},
                "shadow": False,
                "topLevel": False,
            },
        }

    manifest = {
        "type": "llsp3",
        "appType": "scratch",
        "name": "test_scratch",
        "slotIndex": slot,
        "hardware": {"id": "flipper", "type": "flipper"},
        "extensions": ["flipperdisplay"],
        "version": "3.0.0",
    }
    project_json = {
        "targets": [
            {"isStage": True, "name": "Stage", "blocks": {},
             "variables": {}, "lists": {}, "broadcasts": {}},
            {"isStage": False, "name": "Sprite1", "blocks": blocks,
             "variables": {"v1": ["myVar", 0]}, "lists": {},
             "broadcasts": {}},
        ],
        "extensions": ["flipperdisplay"],
        "meta": {"semver": "3.0.0"},
    }

    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w") as z:
        z.writestr("project.json", json.dumps(project_json))

    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("scratch.sb3", inner_buf.getvalue())

    return outer_buf.getvalue()


def write_temp(data: bytes, suffix=".llsp3") -> str:
    """Write data to a temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, data)
    os.close(fd)
    return path


# ── Parse Tests ────────────────────────────────────────────────────

class TestParseLLSP3:
    def test_parse_python_mode(self):
        path = write_temp(make_python_llsp3("import hub\nhub.display.show('X')"))
        try:
            llsp3 = parse_llsp3(path)
            assert llsp3.is_python
            assert not llsp3.is_scratch
            assert llsp3.app_type == "python"
            assert llsp3.name == "test_py"
            assert llsp3.slot_index == 0
            assert llsp3.hardware_type == "gecko-atlantis"
            src = llsp3.get_python_source()
            assert "import hub" in src
            assert "hub.display.show('X')" in src
        finally:
            os.unlink(path)

    def test_parse_scratch_mode(self):
        path = write_temp(make_scratch_llsp3())
        try:
            llsp3 = parse_llsp3(path)
            assert llsp3.is_scratch
            assert not llsp3.is_python
            assert llsp3.app_type == "scratch"
            assert llsp3.name == "test_scratch"
            assert llsp3.hardware_type == "flipper"
            assert llsp3.get_python_source() is None
            blocks = llsp3.get_blocks()
            assert len(blocks) == 2
            opcodes = llsp3.get_opcodes()
            assert "flipperdisplay_displayClear" in opcodes
            assert "flipperevents_whenProgramStarts" in opcodes
        finally:
            os.unlink(path)

    def test_parse_slot_index(self):
        path = write_temp(make_python_llsp3(slot=7))
        try:
            assert parse_llsp3(path).slot_index == 7
        finally:
            os.unlink(path)

    def test_parse_variables(self):
        path = write_temp(make_scratch_llsp3())
        try:
            llsp3 = parse_llsp3(path)
            variables = llsp3.get_variables()
            assert "v1" in variables
            assert variables["v1"] == ["myVar", 0]
        finally:
            os.unlink(path)

    def test_parse_summary(self):
        path = write_temp(make_scratch_llsp3())
        try:
            s = parse_llsp3(path).summary()
            assert s["name"] == "test_scratch"
            assert s["type"] == "scratch"
            assert s["blocks"] == 2
            assert s["variables"] == 1
        finally:
            os.unlink(path)

    def test_parse_missing_file(self):
        with pytest.raises(FileNotFoundError):
            parse_llsp3("/nonexistent/file.llsp3")

    def test_parse_no_manifest(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("random.txt", "hello")
        path = write_temp(buf.getvalue())
        try:
            with pytest.raises(KeyError, match="manifest.json"):
                parse_llsp3(path)
        finally:
            os.unlink(path)

    def test_parse_flat_layout(self):
        """Test projectbody.json layout (no scratch.sb3)."""
        manifest = {
            "type": "llsp3", "appType": "python",
            "name": "flat_test", "slotIndex": 2,
            "hardware": {"id": "gecko-atlantis", "type": "gecko-atlantis"},
            "extensions": [], "version": "3.0.0",
        }
        project = {"main": "print('flat')"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json", json.dumps(manifest))
            z.writestr("projectbody.json", json.dumps(project))
        path = write_temp(buf.getvalue())
        try:
            llsp3 = parse_llsp3(path)
            assert llsp3.is_python
            assert llsp3.get_python_source() == "print('flat')"
        finally:
            os.unlink(path)


# ── Build Tests ────────────────────────────────────────────────────

class TestBuildLLSP3:
    def test_build_python_roundtrip(self):
        source = "import hub\nhub.display.show('OK')"
        data = build_python_llsp3(source, name="roundtrip", slot=3)
        path = write_temp(data)
        try:
            llsp3 = parse_llsp3(path)
            assert llsp3.is_python
            assert llsp3.name == "roundtrip"
            assert llsp3.slot_index == 3
            assert llsp3.get_python_source() == source
        finally:
            os.unlink(path)

    def test_build_python_to_file(self):
        fd, path = tempfile.mkstemp(suffix=".llsp3")
        os.close(fd)
        os.unlink(path)
        try:
            build_python_llsp3("print(1)", out=path)
            assert os.path.exists(path)
            llsp3 = parse_llsp3(path)
            assert llsp3.get_python_source() == "print(1)"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_build_python_hardware_types(self):
        for hw in ("gecko-atlantis", "flipper"):
            data = build_python_llsp3("pass", hardware=hw)
            path = write_temp(data)
            try:
                assert parse_llsp3(path).hardware_type == hw
            finally:
                os.unlink(path)

    def test_build_scratch_blocks(self):
        bb = BlockBuilder()
        start = bb.hat("flipperevents_whenProgramStarts")
        b1 = bb.block("flipperdisplay_displayClear")
        bb.chain(start, b1)

        data = build_scratch_llsp3(bb.blocks, name="scratch_test", slot=1)
        path = write_temp(data)
        try:
            llsp3 = parse_llsp3(path)
            assert llsp3.is_scratch
            assert llsp3.name == "scratch_test"
            assert llsp3.slot_index == 1
            blocks = llsp3.get_blocks()
            assert len(blocks) == 2
        finally:
            os.unlink(path)

    def test_build_scratch_auto_extensions(self):
        bb = BlockBuilder()
        bb.hat("flipperevents_whenProgramStarts")
        bb.block("flipperdisplay_displayClear")
        bb.block("flippermotor_motorStart")
        bb.block("flippersound_beep")

        data = build_scratch_llsp3(bb.blocks)
        path = write_temp(data)
        try:
            llsp3 = parse_llsp3(path)
            exts = llsp3.extensions
            assert "flipperdisplay" in exts
            assert "flippermotor" in exts
            assert "flippersound" in exts
        finally:
            os.unlink(path)


# ── BlockBuilder Tests ─────────────────────────────────────────────

class TestBlockBuilder:
    def test_hat_block(self):
        bb = BlockBuilder()
        h = bb.hat("flipperevents_whenProgramStarts")
        block = bb.blocks[h]
        assert block["opcode"] == "flipperevents_whenProgramStarts"
        assert block["topLevel"] is True
        assert block["next"] is None

    def test_command_block(self):
        bb = BlockBuilder()
        b = bb.block("flipperdisplay_displayClear")
        block = bb.blocks[b]
        assert block["opcode"] == "flipperdisplay_displayClear"
        assert block["topLevel"] is False

    def test_chain(self):
        bb = BlockBuilder()
        h = bb.hat("flipperevents_whenProgramStarts")
        b1 = bb.block("flipperdisplay_displayClear")
        b2 = bb.block("control_wait",
                       inputs={"DURATION": bb.number_input(2)})
        bb.chain(h, b1, b2)

        assert bb.blocks[h]["next"] == b1
        assert bb.blocks[b1]["parent"] == h
        assert bb.blocks[b1]["next"] == b2
        assert bb.blocks[b2]["parent"] == b1
        assert bb.blocks[b2]["next"] is None

    def test_number_input(self):
        assert BlockBuilder.number_input(42) == [1, [4, "42"]]

    def test_text_input(self):
        assert BlockBuilder.text_input("hello") == [1, [10, "hello"]]

    def test_field(self):
        assert BlockBuilder.field("A") == ["A", None]


# ── Scratch-to-Python Compiler Tests ──────────────────────────────

class TestScratchToPython:
    def test_simple_program(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "flipperdisplay_displayClear",
                "next": None, "parent": "hat1",
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert "async def main():" in py
        assert "display.clear()" in py
        assert "runloop.run(main())" in py

    def test_display_text(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "flipperdisplay_displayText",
                "next": None, "parent": "hat1",
                "inputs": {"TEXT": [1, [10, "Hello"]]},
                "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert 'await display.show("Hello")' in py

    def test_motor_start(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "flippermotor_motorStartDirection",
                "next": None, "parent": "hat1",
                "inputs": {
                    "PORT": [1, [10, "A"]],
                    "SPEED": [1, [4, "50"]],
                },
                "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert "motor.run(port.A, 50)" in py

    def test_empty_program(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": None, "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert "async def main():" in py
        assert "pass" in py

    def test_unsupported_opcode(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "some_unknown_opcode",
                "next": None, "parent": "hat1",
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert "# TODO: unsupported opcode: some_unknown_opcode" in py

    def test_multi_block_chain(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "flipperdisplay_displayText",
                "next": "b2", "parent": "hat1",
                "inputs": {"TEXT": [1, [10, "Hi"]]},
                "fields": {},
                "shadow": False, "topLevel": False,
            },
            "b2": {
                "opcode": "control_wait",
                "next": "b3", "parent": "b1",
                "inputs": {"DURATION": [1, [4, "1"]]},
                "fields": {},
                "shadow": False, "topLevel": False,
            },
            "b3": {
                "opcode": "flipperdisplay_displayClear",
                "next": None, "parent": "b2",
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        lines = py.split("\n")
        # Find the order of statements in main()
        main_lines = [l.strip() for l in lines if l.strip() and
                      not l.strip().startswith("#") and
                      not l.strip().startswith("import") and
                      not l.strip().startswith("from") and
                      not l.strip().startswith("async") and
                      not l.strip().startswith("runloop")]
        assert any("display.show" in l for l in main_lines)
        assert any("sleep_ms" in l for l in main_lines)
        assert any("display.clear()" in l for l in main_lines)

    def test_sound_beep(self):
        blocks = {
            "hat1": {
                "opcode": "flipperevents_whenProgramStarts",
                "next": "b1", "parent": None,
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": True,
            },
            "b1": {
                "opcode": "flippersound_beep",
                "next": "b2", "parent": "hat1",
                "inputs": {
                    "NOTE": [1, [4, "60"]],
                    "DURATION": [1, [4, "500"]],
                    "VOLUME": [1, [4, "100"]],
                },
                "fields": {},
                "shadow": False, "topLevel": False,
            },
            "b2": {
                "opcode": "flippersound_stopSound",
                "next": None, "parent": "b1",
                "inputs": {}, "fields": {},
                "shadow": False, "topLevel": False,
            },
        }
        py = scratch_blocks_to_python(blocks)
        assert "await sound.beep(60, 500, 100)" in py
        assert "sound.stop()" in py


# ── Utility Tests ──────────────────────────────────────────────────

class TestUtils:
    def test_nanoid_length(self):
        assert len(_nanoid(20)) == 20
        assert len(_nanoid(12)) == 12

    def test_nanoid_unique(self):
        ids = {_nanoid() for _ in range(100)}
        assert len(ids) == 100

    def test_repr(self):
        path = write_temp(make_scratch_llsp3())
        try:
            llsp3 = parse_llsp3(path)
            r = repr(llsp3)
            assert "test_scratch" in r
            assert "scratch" in r
        finally:
            os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
