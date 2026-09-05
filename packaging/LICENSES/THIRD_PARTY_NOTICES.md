# FastOrder third-party notices

FastOrder bundles the following open-source runtime and build components. The
release process must retain the license texts shipped by each staged wheel in
this directory (or in the installer notice bundle). Versions are pinned by
`requirements.txt` and `packaging/build-requirements.lock`; this index does not
grant permission to replace those versions.

| Component | Pinned version | License / notice source |
| --- | --- | --- |
| aiohttp | 3.14.3 | Apache-2.0, `aiohttp/LICENSE.txt` |
| keyring | 25.7.0 | MIT, `keyring/LICENSE` |
| tzdata | 2026.3 | Apache-2.0, `tzdata/LICENSE` |
| webull-openapi-python-sdk | 2.0.18 | Upstream package license metadata |
| grpcio / protobuf | 1.59.5 / 4.25.9 | Apache-2.0 / BSD-3-Clause |
| paho-mqtt | 1.6.1 | EPL-2.0 and EDL |
| requests / urllib3 / certifi | 2.34.2 / 2.7.0 / 2026.7.22 | Apache-2.0 / MIT / MPL-2.0 |
| cryptography | 41.0.7 | Apache-2.0 or BSD-3-Clause |
| Nuitka / ordered-set / zstandard | 4.2 / 4.1.0 / 0.23.0 | See staged build-wheel license files |

The exact wheel metadata and license files used for a release are part of the
build input. Do not copy credentials, `.env`, runtime databases, or logs into
this notice bundle.
