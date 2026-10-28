"""Download a checksum-verified regional OSM extract without re-dating unchanged files."""
import hashlib
import re

import httpx

from app.core.config import ROOT


def main():
    folder = ROOT / ".local/downloads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "portugal-latest.osm.pbf"
    url = "https://download.geofabrik.de/europe/portugal-latest.osm.pbf"
    with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as client:
        checksum = client.get(url + ".md5")
        checksum.raise_for_status()
        expected = checksum.text.split()[0]
        if not re.fullmatch(r"[a-fA-F0-9]{32}", expected):
            raise ValueError("Invalid provider checksum")
        if path.exists():
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "md5").hexdigest() == expected.lower():
                    print("Existing extract checksum verified; source timestamp preserved")
                    return
        partial = path.with_suffix(".part")
        digest, total = hashlib.md5(), 0
        with client.stream("GET", url) as response, partial.open("wb") as target:
            response.raise_for_status()
            for chunk in response.iter_bytes(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total % (32 * 1024 * 1024) == 0:
                    print(f"Downloaded {total // (1024 * 1024)} MiB", flush=True)
        if digest.hexdigest() != expected.lower():
            raise ValueError("Provider checksum mismatch; previous extract preserved")
        partial.replace(path)
        print("Portugal extract downloaded and verified", total, flush=True)


if __name__ == "__main__":
    main()
