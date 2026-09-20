# Security

Report vulnerabilities privately to
[elliott.phillips.dev@gmail.com](mailto:elliott.phillips.dev@gmail.com).
Include the Flexi version, operating system, reproduction steps, and likely
impact. Use a temporary database with invented records; do not send your
timesheet or credentials. Please avoid public issues for unpatched vulnerabilities.

Security fixes target the current 0.2 release series. The legacy 0.1 release
is not supported.

## Data and network access

Flexi is a single-user local application. Records, settings, and cached bank
holidays live in SQLite. Backups contain the same personal data as the database;
neither is encrypted. Protect them with your operating system's account and disk
encryption. Keep the database on a local filesystem whose locks work correctly.
Shared network filesystems and concurrent use by different OS accounts are not
supported.

Flexi requests bank holidays from `https://www.gov.uk/bank-holidays.json` and
version metadata from `https://pypi.org/pypi/flexi/json`. Requests contain no
timesheet records or application settings, but the servers receive ordinary
connection metadata such as your IP address. There is no telemetry or account.

Database files and configuration are trusted local inputs. Do not replace your
database with a file from an unknown source. YAML configuration uses a safe loader
and validated settings; it does not execute Python objects.

## Release checks

CI audits locked runtime and development dependencies with `pip-audit`, checks
package metadata, and tests the installed wheel. Dependency auditing identifies
published advisories; it does not establish that the application has no security
defects. Report a suspected issue even if these checks pass.

Releases use PyPI trusted publishing and a GitHub environment approval gate.
See [the release procedure](docs/RELEASING.md) for the required account settings.
