---
name: xml-mapper
description: >-
  Map, transform, or convert source data into an XML document that conforms to a target DTD
  (Document Type Definition), then validate it with a pure-Python-stdlib validator (no lxml).
  Use this skill whenever a user wants to fit, shape, mold, coerce, or restructure data (CSV,
  spreadsheets, JSON, a database row, an API payload, or free-form text) into XML that must
  satisfy a given DTD, populate a DTD from data, hand-write XML "to match this DTD," check
  whether an XML file is valid against a DTD, or fix the validity errors in one. Trigger it even
  when the user just provides some data plus a `.dtd` (or an XML doc with a DOCTYPE) and asks to
  "fill it in," "convert it," "make valid XML," or "is this XML valid?" — without saying the
  words "map" or "DTD." Also use it for explaining why an XML document fails DTD validation. The
  skill resolves ambiguous mappings by asking the user targeted questions rather than guessing.
---

# XML — map data into DTD-valid XML

Turn arbitrary source data into an XML document that is **valid** against a target DTD, asking
the user to resolve genuine ambiguities along the way, and validate the result with a validator
that uses only the Python standard library.

The whole point of this skill is to **not silently guess** when a mapping decision is unclear.
A wrong-but-plausible guess that still validates is worse than asking, because it produces
confidently-incorrect data — XML that parses cleanly yet means the wrong thing. So the skill is
built around distinguishing "I can map this confidently" from "this needs the user's call."

## What a DTD constrains (and what it doesn't)
A DTD describes *structure*, not rich data types. It says which elements may appear, how they
nest and in what order, how many of each, and which attributes each element carries (with a
small type system: `CDATA`, enumerations, `ID`/`IDREF`, `NMTOKEN`, etc.). It does **not** check
that a date looks like a date or a number is in range — those live in the element text as plain
characters. Keep this in mind: most of the real mapping judgment is about *placement and
enumerated values*, not format conversion. When a DTD keyword's behavior matters, read
`references/xml-dtd.md`.

## Workflow

### 1. Gather both inputs
You need (a) the source data and (b) the target DTD.
- If a file was uploaded but its content isn't in context, read it (PDF, xlsx, docx, etc. —
  use the appropriate tool for the format).
- If the **DTD is missing**, ask for it. Never invent the target grammar. The DTD may be a
  standalone `.dtd` file or embedded in an example XML doc's `<!DOCTYPE ... [ ... ]>`.
- If the **data is missing**, ask for it.
- Note whether the document will declare the DTD itself (a `<!DOCTYPE>` pointing at the file)
  or whether you're producing a bare fragment to validate against an external `.dtd`.

### 2. Understand the target DTD
Walk the declarations and build a picture of what a valid document looks like:
- For each `<!ELEMENT>`, note its content model: `EMPTY`, `ANY`, **element content**
  (ordered/counted children, no text), or **mixed content** (`#PCDATA` + unordered children).
  Resolve which children are required (`+`, or no operator) vs optional (`?`, `*`).
- For each `<!ATTLIST>`, list every attribute, its **type** (especially enumerations and
  `ID`/`IDREF`), and its **default** (`#REQUIRED`, `#IMPLIED`, `#FIXED`, or a literal default).
- Identify the **root element** (the name the DOCTYPE must use).
- Note any **parameter entities** (`%name;`) or **general entities** (`&name;`) the DTD defines.

When a declaration's semantics are unclear, read `references/xml-dtd.md` for that construct.

### 3. Draft the mapping and classify every target field
For each element and attribute the DTD expects, decide which bucket it falls into:

- **Confident** — an obvious 1:1 match with one sensible interpretation (a source `title`
  column → a `<title>` element; trimming whitespace; an unambiguous source value that exactly
  matches an enumeration token). Map these directly and silently.
- **Needs the user** — see the next section. Collect these into a question batch.
- **Missing** — a required element or `#REQUIRED` attribute with no corresponding source data.
  Always surface these; never fabricate a value to make validation pass.

### 4. Ask about ambiguities — batched, not one at a time
Gather all open questions and ask them together in a single, scannable message (grouped,
numbered, with your best-guess default noted for each). Asking ten questions across ten turns
is exhausting; one well-organized batch respects the user's time. On a chat client, tappable
options are ideal for the enumerated-value questions.

**Ask when:**
- A source field could map to more than one element/attribute, or vice versa.
- A source value must land in an **enumeration** (or `NOTATION`) and the right token isn't
  obvious — e.g. source `"M"` → `(male|female|other)`? `(monthly|...)`? This is the single
  most common DTD ambiguity, the direct analog of a JSON `enum`.
- A **required** element or `#REQUIRED` attribute has no source data — omit it (and fail),
  fill a specific value, or is the source incomplete?
- A **choice** `( a | b )` must be resolved and the data fits more than one branch, or none.
- One source value must be **split** across several elements, or several combined into one
  (e.g. a full name into `<first>`/`<last>`, or an address into structured children).
- Repeated data maps to a `+`/`*` group and the grouping/order isn't clear.
- The source has data with **no home** in the DTD — confirm it should be dropped, since you
  must not add undeclared elements or attributes.
- A `#FIXED` attribute is involved — emit exactly the fixed value; don't take a source value.

**Don't ask when** the answer is unambiguous, already stated, or trivially inferable — that
just adds friction. Calibrate: confident moves are silent; real forks get a question. State any
assumptions you *did* make inline alongside the result, so the user can catch a wrong guess
even on fields you didn't ask about.

### 5. Produce the XML
Build the document from the confident mappings plus the user's answers:
- Respect element **order and occurrence** exactly (sequences are ordered; `+`/none mean at
  least one). In element-content elements, don't put stray text between children.
- Use exact **enumeration tokens** and honor `#FIXED` values.
- Include every `#REQUIRED` attribute; omit `#IMPLIED` ones you have no data for rather than
  emitting empty strings.
- Escape `<`, `&`, and delimiter quotes (`&lt;`, `&amp;`, `&quot;`/`&apos;`); use a `<![CDATA[
  ... ]]>` section for chunks of literal markup.
- If the document should be self-contained, add the `<!DOCTYPE root SYSTEM "grammar.dtd">` (or
  an internal subset) with the correct root name and an XML declaration with the right encoding.

### 6. Validate and report
Run the validator and show the result:

```bash
python scripts/validate.py <instance.xml> <grammar.dtd>
# or, if the instance carries its own <!DOCTYPE>:
python scripts/validate.py <instance.xml>
```

It uses only the Python standard library (`xml.parsers.expat`) — no `lxml`, no `pip install`.
It separates **well-formedness** errors (broken XML — fatal, fix first) from **validity**
errors (well-formed but breaks the DTD), and reports **every** validity error with the path to
the offending element, the rule that failed, and a plain-language message. Add `--json` for
machine-readable output.

- If valid: say so and present the document.
- If not well-formed: fix the syntax (nesting, quoting, entities) first, then re-run.
- If invalid: walk through each error, explain it in plain terms, and either fix it (when the
  fix is unambiguous) or ask the user how to resolve it, then re-validate. Loop until clean or
  the user accepts a remaining gap (e.g. genuinely missing source data).

### 7. Deliver both outputs
Provide the XML **inline** in the chat so the user can read it, **and** save it as a `.xml`
file and present it for download. If validation still has unresolved errors, say so clearly
next to the output rather than implying it's clean.

## Worked example (the asking behavior)

**Source (CSV row):** `name=Jane Doe, joined=03/04/2021, plan=pro, role=admin`
**Target DTD (excerpt):**
```dtd
<!ELEMENT member (firstName, lastName, joined)>
<!ATTLIST member
    tier (free|pro|enterprise) #REQUIRED
    access (read|write|admin) "read">
<!ELEMENT firstName (#PCDATA)>
<!ELEMENT lastName (#PCDATA)>
<!ELEMENT joined (#PCDATA)>
```

Confident: `name` splits into `<firstName>Jane</firstName><lastName>Doe</lastName>` (single
space, obvious); `plan=pro` → `tier="pro"` (exact enum match), mapped silently.
Batched questions:
1. `name=Jane Doe` → split as first **Jane** / last **Doe**? (best guess: yes)
2. `joined=03/04/2021` goes into `<joined>` as plain text — keep as-is, or normalize to
   `YYYY-MM-DD`? If normalizing, is this **March 4** (US `MM/DD`) or **April 3** (`DD/MM`)?
3. `role=admin` → `access` enum. Map to `"admin"`? (best guess: yes)

After answers, produce the XML, validate, deliver inline + file.

## Files
- `scripts/validate.py` — validates an XML instance against a DTD using only the Python
  standard library; separates well-formedness from validity and reports all errors with paths.
- `references/xml-dtd.md` — declaration-by-declaration semantics for mapping data and explaining
  errors (content models, attribute types/defaults, entities, validity traps). Read it when a
  DTD construct's behavior matters.
