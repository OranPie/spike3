"""LLSP3 file format support — parse, build, and upload LEGO SPIKE projects.

An ``.llsp3`` file is a ZIP-inside-ZIP used by the LEGO Education SPIKE App:

    project.llsp3 (ZIP)
    ├── manifest.json       — project metadata (name, type, hub, slot, …)
    └── scratch.sb3 (ZIP)   — inner archive
        └── project.json    — Scratch VM block graph OR Python source

Two project types exist:
  - **Scratch mode** (``appType: "scratch"``): Scratch block graph in project.json
  - **Python mode** (``appType: "python"``): MicroPython source in projectbody.json

This module provides:
  - ``LLSP3File``  — parse and inspect .llsp3 archives
  - ``build_python_llsp3()``  — build a Python-mode .llsp3 from source code
  - Upload helpers that integrate with ``Hub.upload_program()``
"""
from __future__ import annotations

import io
import json
import logging
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("spike3.llsp3")


# ── LLSP3 File Parser ──────────────────────────────────────────────

@dataclass
class LLSP3File:
    """Parsed representation of an .llsp3 project file."""
    path: str
    manifest: dict[str, Any]
    project: dict[str, Any]

    @property
    def name(self) -> str:
        return self.manifest.get("name", "Untitled")

    @property
    def app_type(self) -> str:
        """'scratch' or 'python'."""
        return self.manifest.get("appType", "scratch")

    @property
    def is_python(self) -> bool:
        return self.app_type == "python"

    @property
    def is_scratch(self) -> bool:
        return self.app_type == "scratch"

    @property
    def slot_index(self) -> int:
        return self.manifest.get("slotIndex", 0)

    @property
    def hardware_type(self) -> str:
        hw = self.manifest.get("hardware", {})
        return hw.get("type", hw.get("id", "unknown"))

    @property
    def extensions(self) -> list[str]:
        return self.manifest.get("extensions", [])

    @property
    def version(self) -> str:
        return self.manifest.get("version", "0.0.0")

    # ── Python source extraction ───────────────────────────────────

    def get_python_source(self) -> Optional[str]:
        """Extract MicroPython source code.

        For Python-mode projects, returns the source from projectbody.json.
        For Scratch-mode projects, returns None (use outputllsp3 to decompile).
        """
        if self.is_python:
            return self.project.get("main", None)
        return None

    # ── Scratch block inspection ───────────────────────────────────

    def get_blocks(self) -> dict[str, dict]:
        """Return all Scratch blocks (empty dict for Python-mode projects)."""
        if self.is_scratch:
            targets = self.project.get("targets", [])
            for t in targets:
                blocks = t.get("blocks", {})
                if blocks:
                    return blocks
        return {}

    def get_block_count(self) -> int:
        return len(self.get_blocks())

    def get_opcodes(self) -> list[str]:
        """Return sorted list of unique opcodes used."""
        return sorted(set(
            b.get("opcode", "") for b in self.get_blocks().values()
        ))

    def get_procedures(self) -> list[str]:
        """Return list of custom procedure names."""
        names = []
        for block in self.get_blocks().values():
            if block.get("opcode") == "procedures_prototype":
                names.append(block.get("mutation", {}).get("proccode", ""))
        return names

    def get_variables(self) -> dict[str, Any]:
        """Return variable definitions from Scratch targets."""
        if self.is_scratch:
            targets = self.project.get("targets", [])
            for t in targets:
                v = t.get("variables", {})
                if v:
                    return v
        return {}

    # ── Summary ────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.app_type,
            "hardware": self.hardware_type,
            "slot": self.slot_index,
            "extensions": self.extensions,
            "version": self.version,
            "blocks": self.get_block_count(),
            "procedures": self.get_procedures(),
            "variables": len(self.get_variables()),
        }

    def __repr__(self) -> str:
        return (
            f"LLSP3File(name={self.name!r}, type={self.app_type!r}, "
            f"hardware={self.hardware_type!r}, blocks={self.get_block_count()})"
        )


def parse_llsp3(path: str | Path) -> LLSP3File:
    """Parse an .llsp3 project file.

    Handles both the two-ZIP layout (manifest.json + scratch.sb3) and
    the flat layout (manifest.json + projectbody.json) used by some tools.

    Args:
        path: Path to the .llsp3 file.

    Returns:
        An LLSP3File with parsed manifest and project data.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"LLSP3 file not found: {path}")

    with zipfile.ZipFile(str(path), "r") as outer:
        names = outer.namelist()

        # Parse manifest
        if "manifest.json" not in names:
            raise KeyError(f"manifest.json not found in {path.name}")
        manifest = json.loads(outer.read("manifest.json").decode("utf-8"))

        # Parse project body — two possible layouts
        project: dict[str, Any] = {}

        if "scratch.sb3" in names:
            # Standard LLSP3: ZIP-inside-ZIP
            scratch_data = outer.read("scratch.sb3")
            with zipfile.ZipFile(io.BytesIO(scratch_data), "r") as inner:
                if "project.json" in inner.namelist():
                    project = json.loads(
                        inner.read("project.json").decode("utf-8")
                    )

        elif "projectbody.json" in names:
            # Flat layout (some Python-mode projects)
            project = json.loads(
                outer.read("projectbody.json").decode("utf-8")
            )

    logger.info(f"Parsed {path.name}: type={manifest.get('appType')}, "
                f"hw={manifest.get('hardware', {}).get('type')}")
    return LLSP3File(path=str(path), manifest=manifest, project=project)


# ── LLSP3 Builder ──────────────────────────────────────────────────

def _nanoid(n: int = 20) -> str:
    """Generate a nanoid-like string (used for Scratch block/target IDs)."""
    import random
    import string
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alphabet, k=n))


def build_python_llsp3(
    source: str,
    *,
    name: str = "spike3_program",
    slot: int = 0,
    hardware: str = "gecko-atlantis",
    out: Optional[str | Path] = None,
) -> bytes:
    """Build a Python-mode .llsp3 archive from MicroPython source code.

    This creates a valid .llsp3 file that can be opened in the SPIKE App.

    Args:
        source: MicroPython source code string.
        name: Project name.
        slot: Target program slot (0–19).
        hardware: Hub type string ('gecko-atlantis' for SPIKE 3,
                  'flipper' for SPIKE Prime).
        out: If given, write the .llsp3 file to this path.

    Returns:
        The .llsp3 archive as bytes.
    """
    now_ms = int(time.time() * 1000)

    manifest = {
        "type": "llsp3",
        "appType": "python",
        "id": _nanoid(12),
        "name": name,
        "slotIndex": slot,
        "created": now_ms,
        "lastsaved": now_ms,
        "size": len(source),
        "workspaceX": 0,
        "workspaceY": 0,
        "zoomLevel": 0.75,
        "hardware": {"id": hardware, "type": hardware},
        "state": {"hasMonitors": False},
        "extensions": [],
        "extraFiles": [],
        "autoDelete": False,
        "showAllBlocks": False,
        "version": "3.0.0",
    }

    # Python-mode project body: just the source code
    project_body = {"main": source}

    # Build the inner scratch.sb3 ZIP (contains project.json)
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as inner_zip:
        inner_zip.writestr("project.json", json.dumps(project_body))
    inner_bytes = inner_buf.getvalue()

    # Build the outer .llsp3 ZIP
    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w", zipfile.ZIP_DEFLATED) as outer_zip:
        outer_zip.writestr("manifest.json", json.dumps(manifest, indent=2))
        outer_zip.writestr("scratch.sb3", inner_bytes)
        # Minimal SVG icon
        outer_zip.writestr("icon.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60">'
            '<rect width="60" height="60" fill="#F5A623" rx="8"/>'
            '<text x="30" y="38" text-anchor="middle" fill="white" '
            'font-size="14" font-family="sans-serif">Py</text></svg>')

    result = outer_buf.getvalue()
    logger.info(f"Built Python-mode LLSP3: {name} ({len(result)} bytes)")

    if out is not None:
        Path(out).write_bytes(result)
        logger.info(f"Saved to {out}")

    return result


def build_scratch_llsp3(
    blocks: dict[str, dict],
    *,
    variables: Optional[dict[str, list]] = None,
    name: str = "spike3_scratch",
    slot: int = 0,
    hardware: str = "gecko-atlantis",
    extensions: Optional[list[str]] = None,
    out: Optional[str | Path] = None,
) -> bytes:
    """Build a Scratch-mode .llsp3 archive from a block graph.

    This creates a valid .llsp3 file with Scratch blocks that can be
    opened and edited in the SPIKE App.

    Args:
        blocks: Dict of blockId → block definition (Scratch format).
        variables: Dict of varId → [name, value].
        name: Project name.
        slot: Target program slot.
        hardware: Hub type string.
        extensions: List of extension IDs (auto-detected if None).
        out: If given, write the .llsp3 file to this path.

    Returns:
        The .llsp3 archive as bytes.
    """
    now_ms = int(time.time() * 1000)

    # Auto-detect extensions from block opcodes
    if extensions is None:
        ext_set = set()
        for block in blocks.values():
            opcode = block.get("opcode", "")
            if "_" in opcode:
                prefix = opcode.rsplit("_", 1)[0]
                if prefix.startswith("flipper"):
                    ext_set.add(prefix)
        extensions = sorted(ext_set)

    manifest = {
        "type": "llsp3",
        "appType": "scratch",
        "id": _nanoid(12),
        "name": name,
        "slotIndex": slot,
        "created": now_ms,
        "lastsaved": now_ms,
        "size": 0,
        "workspaceX": 0,
        "workspaceY": 0,
        "zoomLevel": 0.75,
        "hardware": {"id": hardware, "type": hardware},
        "state": {"hasMonitors": False},
        "extensions": extensions,
        "extraFiles": [],
        "autoDelete": False,
        "showAllBlocks": False,
        "version": "3.0.0",
    }

    # Build Scratch project.json
    target = {
        "isStage": False,
        "name": "spike3_generated",
        "blocks": blocks,
        "variables": variables or {},
        "lists": {},
        "broadcasts": {},
        "comments": {},
        "costumes": [],
        "sounds": [],
        "volume": 100,
        "currentCostume": 0,
    }

    project_json = {
        "targets": [
            {
                "isStage": True,
                "name": "Stage",
                "blocks": {},
                "variables": {},
                "lists": {},
                "broadcasts": {},
                "comments": {},
                "costumes": [],
                "sounds": [],
                "volume": 100,
                "currentCostume": 0,
            },
            target,
        ],
        "extensions": extensions,
        "meta": {"semver": "3.0.0"},
        "monitors": [],
    }

    # Build inner ZIP
    inner_buf = io.BytesIO()
    with zipfile.ZipFile(inner_buf, "w", zipfile.ZIP_DEFLATED) as inner_zip:
        inner_zip.writestr("project.json", json.dumps(project_json))
    inner_bytes = inner_buf.getvalue()

    # Build outer ZIP
    outer_buf = io.BytesIO()
    with zipfile.ZipFile(outer_buf, "w", zipfile.ZIP_DEFLATED) as outer_zip:
        outer_zip.writestr("manifest.json", json.dumps(manifest, indent=2))
        outer_zip.writestr("scratch.sb3", inner_bytes)
        outer_zip.writestr("icon.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60">'
            '<rect width="60" height="60" fill="#4C97FF" rx="8"/>'
            '<text x="30" y="38" text-anchor="middle" fill="white" '
            'font-size="12" font-family="sans-serif">Scratch</text></svg>')

    result = outer_buf.getvalue()
    manifest["size"] = len(result)
    logger.info(f"Built Scratch-mode LLSP3: {name} ({len(result)} bytes, "
                f"{len(blocks)} blocks)")

    if out is not None:
        Path(out).write_bytes(result)
        logger.info(f"Saved to {out}")

    return result


# ── Scratch Block Helpers ──────────────────────────────────────────
# Minimal helpers to build Scratch block graphs programmatically
# (without requiring the full outputllsp3 package)

class BlockBuilder:
    """Simple builder for Scratch block graphs.

    Produces block dicts compatible with the SPIKE App's project.json format.
    For complex projects, use the full ``outputllsp3`` package instead.

    Example::

        bb = BlockBuilder()
        start = bb.hat("flipperevents_whenProgramStarts")
        b1 = bb.block("flipperdisplay_displayText",
                       inputs={"TEXT": bb.text_input("Hello!")})
        b2 = bb.block("control_wait",
                       inputs={"DURATION": bb.number_input(1)})
        b3 = bb.block("flipperdisplay_displayClear")
        bb.chain(start, b1, b2, b3)

        blocks = bb.blocks  # dict ready for build_scratch_llsp3()
    """

    def __init__(self):
        self.blocks: dict[str, dict] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"b{self._counter}_{_nanoid(8)}"

    def hat(self, opcode: str, *, fields: Optional[dict] = None,
            x: int = 50, y: int = 50) -> str:
        """Create a hat (event trigger) block."""
        bid = self._next_id()
        self.blocks[bid] = {
            "opcode": opcode,
            "next": None,
            "parent": None,
            "inputs": {},
            "fields": fields or {},
            "shadow": False,
            "topLevel": True,
            "x": x,
            "y": y,
        }
        return bid

    def block(self, opcode: str, *, inputs: Optional[dict] = None,
              fields: Optional[dict] = None) -> str:
        """Create a command block."""
        bid = self._next_id()
        self.blocks[bid] = {
            "opcode": opcode,
            "next": None,
            "parent": None,
            "inputs": inputs or {},
            "fields": fields or {},
            "shadow": False,
            "topLevel": False,
        }
        return bid

    def chain(self, *block_ids: str) -> None:
        """Link blocks sequentially: b1 → b2 → b3 → …"""
        for i in range(len(block_ids) - 1):
            curr = block_ids[i]
            nxt = block_ids[i + 1]
            self.blocks[curr]["next"] = nxt
            self.blocks[nxt]["parent"] = curr

    # ── Input helpers ──────────────────────────────────────────────

    @staticmethod
    def number_input(value: float) -> list:
        """Create a number literal input: [1, [4, "value"]]."""
        return [1, [4, str(value)]]

    @staticmethod
    def text_input(value: str) -> list:
        """Create a text literal input: [1, [10, "value"]]."""
        return [1, [10, str(value)]]

    @staticmethod
    def field(value: str) -> list:
        """Create a field value: [value, null]."""
        return [value, None]


# ── Scratch-to-MicroPython Compiler ───────────────────────────────
# Translates Scratch block graphs to hub MicroPython for direct upload.
# This allows uploading .llsp3 Scratch projects to run standalone on the hub.

# Opcode → MicroPython code template mapping
_SCRATCH_TO_PYTHON: dict[str, str] = {
    # Motor
    "flippermotor_motorTurnForDirection":
        "await motor.run_for_degrees(port.{PORT}, {VALUE}, {SPEED})",
    "flippermotor_motorTurnForDirectionTime":
        "await motor.run_for_time(port.{PORT}, {VALUE}, {SPEED})",
    "flippermotor_motorGoDirectionToPosition":
        "await motor.run_to_absolute_position(port.{PORT}, {POSITION}, {SPEED})",
    "flippermotor_motorStartDirection":
        "motor.run(port.{PORT}, {SPEED})",
    "flippermotor_motorStop":
        "motor.stop(port.{PORT})",
    "flippermotor_motorSetSpeed":
        "motor.set_default_speed(port.{PORT}, {SPEED})",
    "flippermotor_motorSetPosition":
        "motor.reset_relative_position(port.{PORT}, {POSITION})",
    # Display
    "flipperdisplay_displayText":
        "await display.show(\"{TEXT}\")",
    "flipperdisplay_displayImageFor":
        "display.show_image({IMAGE})\nawait runloop.sleep_ms({DURATION})",
    "flipperdisplay_displayImage":
        "display.show_image({IMAGE})",
    "flipperdisplay_displayClear":
        "display.clear()",
    "flipperdisplay_displaySetPixel":
        "display.pixel({X}, {Y}, {BRIGHTNESS})",
    # Sound
    "flippersound_beep":
        "await sound.beep({NOTE}, {DURATION}, {VOLUME})",
    "flippersound_stopSound":
        "sound.stop()",
    # Sensors / IMU
    "flipperimu_resetYaw":
        "motion_sensor.reset_yaw()",
    # Light
    "flipperlight_centerButtonLight":
        "light.color(light.POWER, color.{COLOR})",
    # Control flow
    "control_wait":
        "await runloop.sleep_ms(int({DURATION} * 1000))",
    "control_repeat":
        "for _ in range(int({TIMES})):",
    "control_forever":
        "while True:",
}


def scratch_blocks_to_python(blocks: dict[str, dict]) -> str:
    """Convert a Scratch block graph to MicroPython source code.

    This is a *best-effort* compiler for simple Scratch programs. Complex
    programs with nested logic, variables, procedures, etc. should use the
    full ``outputllsp3`` exporter instead.

    Args:
        blocks: Scratch block dict (from project.json targets[].blocks).

    Returns:
        MicroPython source code string suitable for hub upload.
    """
    # Find top-level hat blocks (entry points)
    hat_ids = [
        bid for bid, b in blocks.items()
        if b.get("topLevel") and b.get("opcode", "").startswith("flipperevents_")
    ]

    if not hat_ids:
        # No hat blocks — try to find any top-level chain
        hat_ids = [bid for bid, b in blocks.items() if b.get("topLevel")]

    lines = [
        "# Auto-generated from Scratch blocks by spike3",
        "import runloop",
        "from hub import port, motor, display, sound, light, motion_sensor, color",
        "",
    ]

    for i, hat_id in enumerate(hat_ids):
        func_name = f"main" if i == 0 else f"program_{i}"
        lines.append(f"async def {func_name}():")

        # Walk the block chain
        current_id = blocks[hat_id].get("next")
        indent = "    "
        body_lines = []

        while current_id and current_id in blocks:
            block = blocks[current_id]
            opcode = block.get("opcode", "")
            code = _compile_block(block, blocks)
            if code:
                for line in code.split("\n"):
                    body_lines.append(f"{indent}{line}")
            current_id = block.get("next")

        if not body_lines:
            body_lines = [f"{indent}pass"]

        lines.extend(body_lines)
        lines.append("")

    lines.append("runloop.run(main())")
    return "\n".join(lines)


def _resolve_input(block: dict, blocks: dict, input_name: str,
                   default: str = "0") -> str:
    """Resolve a block input to its value string."""
    inputs = block.get("inputs", {})
    if input_name not in inputs:
        return default

    inp = inputs[input_name]
    # inp format: [type, value_or_id, shadow?]
    if len(inp) >= 2:
        val = inp[1]
        if isinstance(val, list):
            # Literal: [type_code, "value"]
            return str(val[1]) if len(val) >= 2 else default
        elif isinstance(val, str) and val in blocks:
            # Reference to another block (reporter) — simplify
            ref_block = blocks[val]
            ref_opcode = ref_block.get("opcode", "")
            # Try to resolve reporter blocks
            if ref_opcode == "math_number":
                fields = ref_block.get("fields", {})
                return str(fields.get("NUM", ["0"])[0])
            elif ref_opcode == "text":
                fields = ref_block.get("fields", {})
                return str(fields.get("TEXT", [""])[0])
            return default
        elif isinstance(val, str):
            return val
    return default


def _resolve_field(block: dict, field_name: str, default: str = "") -> str:
    """Resolve a block field to its value string."""
    fields = block.get("fields", {})
    if field_name in fields:
        val = fields[field_name]
        if isinstance(val, list) and val:
            return str(val[0])
        return str(val)
    return default


def _compile_block(block: dict, blocks: dict) -> Optional[str]:
    """Compile a single Scratch block to MicroPython code."""
    opcode = block.get("opcode", "")

    template = _SCRATCH_TO_PYTHON.get(opcode)
    if template is None:
        return f"# TODO: unsupported opcode: {opcode}"

    # Resolve all template placeholders from inputs and fields
    result = template
    for placeholder in _extract_placeholders(template):
        # Try field first, then input
        value = _resolve_field(block, placeholder)
        if not value:
            value = _resolve_input(block, blocks, placeholder, default="0")
        result = result.replace(f"{{{placeholder}}}", value)

    return result


def _extract_placeholders(template: str) -> list[str]:
    """Extract {PLACEHOLDER} names from a template string."""
    import re
    return re.findall(r"\{([A-Z_]+)\}", template)


# ── Hub Upload Integration ─────────────────────────────────────────

def upload_llsp3(hub, path: str | Path, *, slot: Optional[int] = None,
                 compile_scratch: bool = True,
                 on_progress=None,
                 timeout: float = 20.0) -> dict[str, Any]:
    """Upload an .llsp3 project to the hub.

    For Python-mode projects: extracts source and uploads directly.
    For Scratch-mode projects: compiles blocks to MicroPython (if
    compile_scratch=True) or raises an error.

    Args:
        hub: A connected spike3.Hub instance.
        path: Path to the .llsp3 file.
        slot: Override the slot index from the manifest.
        compile_scratch: If True, compile Scratch blocks to Python.
        on_progress: Optional progress callback(bytes_sent, total).
        timeout: Per-chunk upload timeout.

    Returns:
        Dict with upload result info.
    """
    llsp3 = parse_llsp3(path)
    target_slot = slot if slot is not None else llsp3.slot_index

    if llsp3.is_python:
        source = llsp3.get_python_source()
        if not source:
            raise ValueError(f"Python-mode project {path} has no source code")
        logger.info(f"Uploading Python program from {llsp3.name} "
                    f"({len(source)} chars) to slot {target_slot}")
    elif llsp3.is_scratch:
        if not compile_scratch:
            raise ValueError(
                f"Scratch-mode project {path} requires compilation. "
                f"Set compile_scratch=True or use outputllsp3 to export."
            )
        blocks = llsp3.get_blocks()
        if not blocks:
            raise ValueError(f"Scratch-mode project {path} has no blocks")
        source = scratch_blocks_to_python(blocks)
        logger.info(f"Compiled {len(blocks)} Scratch blocks to Python "
                    f"({len(source)} chars) for slot {target_slot}")
    else:
        raise ValueError(f"Unsupported project type: {llsp3.app_type}")

    data = source.encode("utf-8")
    hub.upload_program("program.py", data, slot=target_slot,
                       on_progress=on_progress, timeout=timeout)

    return {
        "name": llsp3.name,
        "type": llsp3.app_type,
        "slot": target_slot,
        "source_size": len(source),
        "uploaded_bytes": len(data),
        "compiled": llsp3.is_scratch,
    }


def upload_python_file(hub, path: str | Path, *, slot: int = 0,
                       on_progress=None,
                       timeout: float = 20.0) -> dict[str, Any]:
    """Upload a .py file directly to the hub.

    Args:
        hub: A connected spike3.Hub instance.
        path: Path to the Python source file.
        slot: Target program slot.
        on_progress: Optional progress callback.
        timeout: Per-chunk upload timeout.

    Returns:
        Dict with upload result info.
    """
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    data = source.encode("utf-8")

    logger.info(f"Uploading {path.name} ({len(data)} bytes) to slot {slot}")
    hub.upload_program("program.py", data, slot=slot,
                       on_progress=on_progress, timeout=timeout)

    return {
        "name": path.stem,
        "type": "python",
        "slot": slot,
        "source_size": len(source),
        "uploaded_bytes": len(data),
    }


def upload_python_string(hub, source: str, *, name: str = "program",
                         slot: int = 0, on_progress=None,
                         timeout: float = 20.0) -> dict[str, Any]:
    """Upload a Python source string directly to the hub.

    Args:
        hub: A connected spike3.Hub instance.
        source: MicroPython source code.
        name: Program name (for logging).
        slot: Target program slot.
        on_progress: Optional progress callback.
        timeout: Per-chunk upload timeout.

    Returns:
        Dict with upload result info.
    """
    data = source.encode("utf-8")
    logger.info(f"Uploading '{name}' ({len(data)} bytes) to slot {slot}")
    hub.upload_program("program.py", data, slot=slot,
                       on_progress=on_progress, timeout=timeout)

    return {
        "name": name,
        "type": "python",
        "slot": slot,
        "source_size": len(source),
        "uploaded_bytes": len(data),
    }
