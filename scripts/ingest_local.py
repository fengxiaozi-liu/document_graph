#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


SUPPORTED_EXTS = {".md", ".txt", ".html", ".htm"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_from_epoch_seconds(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def sha1_hex_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha1_hex_text(text: str) -> str:
    return sha1_hex_bytes(text.encode("utf-8", errors="replace"))


def stable_doc_id(relative_path: str) -> str:
    # Stable across runs as long as the relative path is stable.
    # Versioning and incremental updates are tracked separately via mtime/size/content_hash.
    return f"local_{sha1_hex_text(relative_path)}"


def safe_relpath(path: Path, base_dir: Path) -> str:
    rel = path.resolve().relative_to(base_dir.resolve())
    return rel.as_posix()


def read_bytes(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ManifestEntry:
    doc_id: str
    relative_path: str
    file_type: str
    mtime: float
    size_bytes: int
    export_format: str
    local_path: str
    content_hash: str
    synced_at: str


def load_manifest_index(manifest_path: Path) -> dict[str, ManifestEntry]:
    index: dict[str, ManifestEntry] = {}
    if not manifest_path.exists():
        return index
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entry = ManifestEntry(**data)
                index[entry.doc_id] = entry
            except Exception:
                # Best-effort: ignore malformed lines.
                continue
    return index


def iter_source_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        yield path


def write_json(path: Path, obj: object) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def append_jsonl(path: Path, obj: object) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
        f.write("\n")


def export_one(
    *,
    source_path: Path,
    input_dir: Path,
    output_dir: Path,
    manifest_path: Path,
    manifest_index: dict[str, ManifestEntry],
    force_export_format: Optional[str],
    dry_run: bool,
) -> bool:
    relative_path = safe_relpath(source_path, input_dir)
    doc_id = stable_doc_id(relative_path)

    stat = source_path.stat()
    mtime = stat.st_mtime
    size_bytes = stat.st_size

    previous = manifest_index.get(doc_id)
    if previous and previous.mtime == mtime and previous.size_bytes == size_bytes:
        return False

    src_bytes = read_bytes(source_path)
    content_hash = sha1_hex_bytes(src_bytes)

    if previous and previous.content_hash == content_hash:
        # If content is unchanged, refresh manifest so synced_at is accurate (optional),
        # but avoid rewriting content/meta.
        entry = ManifestEntry(
            doc_id=doc_id,
            relative_path=relative_path,
            file_type=source_path.suffix.lower().lstrip("."),
            mtime=mtime,
            size_bytes=size_bytes,
            export_format=previous.export_format,
            local_path=previous.local_path,
            content_hash=content_hash,
            synced_at=utc_now_iso(),
        )
        if not dry_run:
            append_jsonl(manifest_path, asdict(entry))
        return True

    export_ext = (
        f".{force_export_format.lstrip('.')}"
        if force_export_format
        else source_path.suffix.lower()
    )
    export_format = export_ext.lstrip(".")

    out_input_dir = output_dir / "input"
    out_meta_dir = output_dir / "meta"
    ensure_dir(out_input_dir)
    ensure_dir(out_meta_dir)

    out_content_path = out_input_dir / f"{doc_id}{export_ext}"
    out_meta_path = out_meta_dir / f"{doc_id}.json"

    meta = {
        "doc_id": doc_id,
        "title": source_path.name,
        "source_type": "local_fs",
        "source_uri": relative_path,
        "updated_time": iso_from_epoch_seconds(mtime),
        "export_format": export_format,
        "local_path": str(out_content_path.as_posix()),
        "acl": "public",
        "content_hash": content_hash,
        "size_bytes": size_bytes,
    }

    entry = ManifestEntry(
        doc_id=doc_id,
        relative_path=relative_path,
        file_type=source_path.suffix.lower().lstrip("."),
        mtime=mtime,
        size_bytes=size_bytes,
        export_format=export_format,
        local_path=str(out_content_path.as_posix()),
        content_hash=content_hash,
        synced_at=utc_now_iso(),
    )

    if dry_run:
        return True

    # Keep MVP simple: copy bytes as-is. If force_export_format is set, it only changes the extension.
    out_content_path.write_bytes(src_bytes)
    write_json(out_meta_path, meta)
    append_jsonl(manifest_path, asdict(entry))
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest local documents into a GraphRAG-friendly input folder + metadata."
    )
    parser.add_argument(
        "--input-dir",
        default="input",
        help="Directory containing source documents (default: ./input).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/local_export",
        help="Output directory (default: data/local_export).",
    )
    parser.add_argument(
        "--force-export-format",
        default=None,
        help="Force output extension (e.g. md, txt, html). Content is copied as-is; only extension changes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    force_export_format = args.force_export_format
    dry_run = bool(args.dry_run)

    if force_export_format:
        force_export_format = force_export_format.lower().lstrip(".")

    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"input dir not found: {input_dir}")

    ensure_dir(output_dir)
    manifest_path = output_dir / "manifest.jsonl"
    manifest_index = load_manifest_index(manifest_path)

    updated = 0
    skipped = 0
    total = 0

    for source_path in iter_source_files(input_dir):
        total += 1
        changed = export_one(
            source_path=source_path,
            input_dir=input_dir,
            output_dir=output_dir,
            manifest_path=manifest_path,
            manifest_index=manifest_index,
            force_export_format=force_export_format,
            dry_run=dry_run,
        )
        if changed:
            updated += 1
        else:
            skipped += 1

    print(
        json.dumps(
            {
                "input_dir": str(input_dir.as_posix()),
                "output_dir": str(output_dir.as_posix()),
                "supported_exts": sorted(SUPPORTED_EXTS),
                "total_seen": total,
                "updated": updated,
                "skipped": skipped,
                "dry_run": dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

