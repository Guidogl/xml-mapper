# xml-mapper

[![skills.sh](https://skills.sh/b/guidogl/xml-mapper)](https://skills.sh/guidogl/xml-mapper)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](./LICENSE.txt)
[![Agent Skills Spec](https://img.shields.io/badge/Agent%20Skills-Specification-blue)](https://agentskills.io)
[![XML](https://img.shields.io/badge/XML-1.0%20%2B%20DTD-orange)](https://www.w3.org/TR/xml/)
[![Python](https://img.shields.io/badge/python-3.x-blue?logo=python&logoColor=white)](https://www.python.org)

An agent skill that maps, transforms, and validates arbitrary source data into XML that
conforms to a target DTD (Document Type Definition).

## What it does

Turn arbitrary source data (CSV, spreadsheets, JSON, a database row, an API payload, or
free-form text) into an XML document that is valid against a given DTD — then validate it with
a validator that uses only the Python standard library (no lxml). The skill's core principle is
to **not silently guess**: when a mapping decision is genuinely ambiguous (element vs.
attribute, content-model ordering, enumerated attribute values, missing required content), it
asks the user targeted, batched questions instead of producing confidently-incorrect data. It
also explains why an XML document fails DTD validation.

## Install

**skills.sh**

```bash
npx skills add guidogl/xml-mapper
```

**Claude Code plugin marketplace**

```
/plugin marketplace add guidogl/xml-mapper
```

## Contents

- `SKILL.md` — the skill instructions and workflow.
- `references/xml-dtd.md` — DTD construct reference for mapping data and explaining validation
  errors.
- `scripts/validate.py` — validates an XML instance against a DTD using only the Python
  standard library and reports every error with its location and a message.
- `evals/evals.json` — evaluation cases for the skill.

## Requirements

The validator uses only the Python standard library — no third-party packages required.

## License

Apache-2.0 — see [`LICENSE.txt`](./LICENSE.txt).
