"""Install a checked voice archive shared by the two owner applications."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha256")
    args = parser.parse_args()
    archive = Path.home() / "deploy/voice-models.tar.gz"
    with archive.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != args.sha256:
            raise ValueError("Voice archive checksum mismatch")
    names = {"sentinel/data/models/kokoro/" + n for n in ("kokoro-v1.0.onnx", "voices-v1.0.bin")}
    root = Path.home() / "apps"
    with tarfile.open(archive) as bundle:
        if {m.name for m in bundle.getmembers()} != names or not all(m.isfile() for m in bundle.getmembers()):
            raise ValueError("Unexpected voice archive contents")
        bundle.extractall(root, filter="data")
    target = root / "aria/data/models/kokoro"
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = root / name
        shutil.copy2(source, target / source.name)
    print("Verified voice weights installed for both apps")


if __name__ == "__main__":
    main()
