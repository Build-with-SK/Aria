"""Verify and install the selected latest model files, with safe extraction."""
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    root = Path.home() / "apps"
    deploy = Path.home() / "deploy"
    manifest = json.loads((deploy / "latest-ml-models.manifest.json").read_text())
    files = manifest.get("files", manifest)
    with tarfile.open(deploy / "latest-ml-models.tar.gz") as archive:
        for name, expected in files.items():
            parts = Path(name).parts
            member = archive.getmember(name)
            digest = expected.get("sha256") if isinstance(expected, dict) else expected
            if parts[:3] != ("aria", "data", "models") or ".." in parts or not member.isfile():
                raise ValueError("Unexpected model member")
            if hashlib.sha256(archive.extractfile(member).read()).hexdigest() != digest:
                raise ValueError("Model checksum mismatch")
        archive.extractall(root, members=[archive.getmember(n) for n in files], filter="data")
    print(json.dumps({"verified_model_files": len(files)}))


if __name__ == "__main__":
    main()
