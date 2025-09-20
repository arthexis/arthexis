# Third-Party License Notices

This project is distributed under the GNU General Public License version 3.0. In addition to the
project's own license, the following third-party components are used at runtime. Their license
texts are included in this repository so that downstream redistributors can comply with the
relevant notice and weak-copyleft obligations.

## psycopg & psycopg-binary — GNU Lesser General Public License v3

* Location of license text: [`docs/legal/licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)
* Compliance notes:
  * When distributing binary builds, provide a clear path for recipients to relink the binaries
    against their own builds of `psycopg`.
  * Make the corresponding source code for `psycopg` available (for example by referencing the
    upstream project) for as long as you distribute binaries.

## python-crontab — GNU Lesser General Public License v3

* Location of license text: [`docs/legal/licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)
* Compliance notes:
  * Provide prominent notice that `python-crontab` is covered by the LGPLv3.
  * Ensure downstream recipients receive or can obtain the library's complete source code.

## certifi — Mozilla Public License 2.0

* Location of license text: [`docs/legal/licenses/MPL-2.0.txt`](licenses/MPL-2.0.txt)
* Compliance notes:
  * Preserve the MPL 2.0 license text in any binary or source distributions.
  * Keep any changes to `certifi` files (if you distribute modified copies) under the MPL 2.0 and
    publish the modified source code.

## mfrc522 — GNU General Public License v3

* License text: provided at the repository root in [`LICENSE`](../../LICENSE)
* Compliance notes:
  * Because `mfrc522` is GPL-licensed, the combined work is distributed under the same GPLv3
    terms as of this change. Distribute complete corresponding source code alongside any binaries.

For questions about license compliance or additional third-party components, please contact the
release managers listed in the administrative documentation.
