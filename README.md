# edgestack-helm — dev channel

Charts built from each component's main branch, all at version `0.0.0-dev`
(`appVersion: dev`, the build commit in `annotations.petasus.io/revision`). They are
overwritten on every build. Releases live on `master` (the stable channel), which only
grows.

    helm repo add edgestack-dev https://raw.githubusercontent.com/petasus-ai/edgestack-helm/dev/
    helm pull edgestack-dev/<chart> --version 0.0.0-dev

`.github/workflows/update-index.yml` and `.github/scripts/update-index.py` are the same
files as on `master`; change both branches together.
