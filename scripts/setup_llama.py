"""Download the selected official runtime/model; weights never enter Git."""

import hashlib
import json
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"


def download(entry):
    folder = LOCAL / entry.get("directory", "downloads")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / entry["name"]
    if not target.exists():
        partial = target.with_suffix(target.suffix + ".partial")
        print(f"Downloading {entry['name']}", flush=True)
        size = entry["size"]
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            raise ValueError(f"Oversized partial download: {partial}")
        chunk_size = (8 if entry["name"].endswith(".gguf") else 1) * 1024 * 1024
        ranges = [
            (start, min(start + chunk_size, size) - 1) for start in range(offset, size, chunk_size)
        ]

        def fetch(bounds):
            start, end = bounds
            expected = f"bytes {start}-{end}/{size}"
            for attempt in range(3):
                try:
                    request = urllib.request.Request(
                        entry["url"], headers={"Range": f"bytes={start}-{end}"}
                    )
                    with urllib.request.urlopen(request, timeout=30) as response:
                        if (
                            response.status != 206
                            or response.headers.get("Content-Range") != expected
                        ):
                            raise ValueError("Download server did not honor the byte range")
                        data = response.read(end - start + 2)
                    if len(data) != end - start + 1:
                        raise ValueError("Incomplete byte range")
                    return data
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)

        # Ordered append leaves a resumable contiguous prefix on interruption.
        with ThreadPoolExecutor(max_workers=6) as pool, partial.open("ab") as out:
            for index in range(0, len(ranges), 6):
                for chunk in pool.map(fetch, ranges[index : index + 6]):
                    out.write(chunk)
                    out.flush()
                    offset += len(chunk)
                    if offset % (32 * 1024 * 1024) < len(chunk) or offset == size:
                        print(f"{entry['name']}: {offset * 100 // size}%", flush=True)
        partial.rename(target)
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != entry["sha256"]:
        raise ValueError(f"Checksum mismatch: {target}. Remove this file before retrying.")
    print(f"SHA256 verified: {entry['name']}", flush=True)
    return entry, target


def main():
    manifest = json.loads((ROOT / "data" / "llm-runtime.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(download, manifest["files"]))
    for entry, path in files:
        if "extract_to" not in entry:
            continue
        destination = (LOCAL / entry["extract_to"]).resolve()
        if not destination.is_relative_to(LOCAL.resolve()):
            raise ValueError("Archive target is outside .local")
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not (destination / member.filename).resolve().is_relative_to(destination):
                    raise ValueError("Archive member is outside the runtime directory")
            archive.extractall(destination)
    print(f"Ready: llama.cpp {manifest['llama_cpp_version']}, {manifest['model']}")


if __name__ == "__main__":
    main()
