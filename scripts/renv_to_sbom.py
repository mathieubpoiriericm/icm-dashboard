"""Convert renv.lock to a CycloneDX 1.6 SBOM.

Reads the renv lockfile and emits a CycloneDX 1.6 JSON document with
one ``library`` component per package, ``pkg:cran/<name>@<version>``
purls, license metadata, and a dependency graph derived from
``Depends`` / ``Imports`` / ``LinkingTo``.

Output is reproducible by default (no timestamp, no random serial) so
the SBOM can be committed and diffed across ``renv::snapshot()`` runs.

Usage:
    python scripts/renv_to_sbom.py
    python scripts/renv_to_sbom.py --input renv.lock --output sbom.renv.cdx.json
    python scripts/renv_to_sbom.py --root-name my-app --root-version 1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Final, TypedDict

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# INPUT SHAPE
# ----------------------------------------------------------------------


class RenvPackage(TypedDict, total=False):
    Package: str
    Version: str
    Source: str
    Repository: str
    Title: str
    License: str
    Author: str
    URL: str
    BugReports: str
    Depends: list[str]
    Imports: list[str]
    LinkingTo: list[str]


# ----------------------------------------------------------------------
# CONSTANTS
# ----------------------------------------------------------------------


SPEC_VERSION: Final[str] = "1.6"
DEFAULT_INPUT: Final[str] = "renv.lock"
DEFAULT_OUTPUT: Final[str] = "sbom.renv.cdx.json"
DEFAULT_ROOT_NAME: Final[str] = "rshiny-dashboard"
TOOL_NAME: Final[str] = "renv_to_sbom"
TOOL_VERSION: Final[str] = "1.0"
DEP_FIELDS: Final[tuple[str, ...]] = ("Depends", "Imports", "LinkingTo")
DEP_VERSION_RE: Final[re.Pattern[str]] = re.compile(r"\(.*?\)")


# ----------------------------------------------------------------------
# CONVERSION HELPERS
# ----------------------------------------------------------------------


def _purl(name: str, version: str) -> str:
    return f"pkg:cran/{name}@{version}"


def _parse_deps(field: list[str] | None) -> list[str]:
    if not field:
        return []
    out: list[str] = []
    for item in field:
        name = DEP_VERSION_RE.sub("", item).strip()
        if name and name != "R":
            out.append(name)
    return out


def _licenses(field: str | None) -> list[dict[str, Any]]:
    # R license strings are free-form ("MIT + file LICENSE",
    # "GPL (>= 2) | MIT"). Keep the raw expression rather than guess
    # an SPDX identifier — downstream tooling can normalize if needed.
    if not field:
        return []
    parts = [p.strip() for p in field.split("|") if p.strip()]
    return [{"license": {"name": p}} for p in parts]


def _ext_refs(pkg: RenvPackage, cran_url: str | None) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if url := pkg.get("URL"):
        for raw in re.split(r"[,\s]+", url):
            u = raw.strip()
            if u.startswith(("http://", "https://")):
                refs.append({"type": "website", "url": u})
    if bug := pkg.get("BugReports"):
        refs.append({"type": "issue-tracker", "url": bug})
    if pkg.get("Repository") == "CRAN" and cran_url:
        base = cran_url.rstrip("/")
        refs.append(
            {
                "type": "distribution",
                "url": f"{base}/web/packages/{pkg['Package']}/index.html",
            }
        )
    return refs


def _component(pkg: RenvPackage, cran_url: str | None) -> dict[str, Any]:
    ref = _purl(pkg["Package"], pkg["Version"])
    comp: dict[str, Any] = {
        "type": "library",
        "bom-ref": ref,
        "name": pkg["Package"],
        "version": pkg["Version"],
        "purl": ref,
    }
    if title := pkg.get("Title"):
        comp["description"] = " ".join(title.split())
    if author := pkg.get("Author"):
        comp["author"] = " ".join(author.split())
    if licenses := _licenses(pkg.get("License")):
        comp["licenses"] = licenses
    if refs := _ext_refs(pkg, cran_url):
        comp["externalReferences"] = refs
    return comp


def build_sbom(
    lock: dict[str, Any],
    *,
    root_name: str,
    root_version: str | None,
) -> dict[str, Any]:
    """Return a CycloneDX 1.6 SBOM dict built from a parsed renv lockfile."""
    pkgs: dict[str, RenvPackage] = lock["Packages"]
    known = set(pkgs)
    repos = {r["Name"]: r["URL"] for r in lock["R"].get("Repositories", [])}
    cran_url = repos.get("CRAN")

    components = [_component(pkgs[name], cran_url) for name in sorted(pkgs)]

    dep_edges: list[dict[str, Any]] = []
    for name in sorted(pkgs):
        pkg = pkgs[name]
        ref = _purl(pkg["Package"], pkg["Version"])
        edges: set[str] = set()
        for field in DEP_FIELDS:
            for dep_name in _parse_deps(pkg.get(field)):
                if dep_name in known:
                    edges.add(_purl(dep_name, pkgs[dep_name]["Version"]))
        dep_edges.append({"ref": ref, "dependsOn": sorted(edges)})

    root_component: dict[str, Any] = {
        "type": "application",
        "bom-ref": "root",
        "name": root_name,
        "description": "Generated from renv.lock",
    }
    if root_version:
        root_component["version"] = root_version

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": {
            "component": root_component,
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": TOOL_NAME,
                        "version": TOOL_VERSION,
                        "description": "renv.lock to CycloneDX 1.6 converter",
                    }
                ]
            },
            "properties": [
                {"name": "cdx:reproducible", "value": "true"},
                {"name": "cdx:r:version", "value": lock["R"]["Version"]},
            ],
        },
        "components": components,
        "dependencies": [
            {"ref": "root", "dependsOn": [c["bom-ref"] for c in components]},
            *dep_edges,
        ],
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert renv.lock to a CycloneDX 1.6 SBOM.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(DEFAULT_INPUT),
        help=f"Path to the renv lockfile (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(DEFAULT_OUTPUT),
        help=f"Path to write the CycloneDX JSON SBOM (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--root-name",
        default=DEFAULT_ROOT_NAME,
        help=f"Name of the root application component (default: {DEFAULT_ROOT_NAME}).",
    )
    parser.add_argument(
        "--root-version",
        default=None,
        help="Optional version for the root application component.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    lock = json.loads(args.input.read_text(encoding="utf-8"))
    sbom = build_sbom(lock, root_name=args.root_name, root_version=args.root_version)
    args.output.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    n_comp = len(sbom["components"])
    n_edges = sum(len(d["dependsOn"]) for d in sbom["dependencies"][1:])
    logger.info("Wrote %s: %d components, %d dep edges", args.output, n_comp, n_edges)
    return 0


if __name__ == "__main__":
    sys.exit(main())
