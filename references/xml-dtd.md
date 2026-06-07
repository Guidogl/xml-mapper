# XML 1.0 DTD — reference for producing valid documents

Read this when the target DTD uses a declaration you need to map data into, or
when you need to explain a validity error from `scripts/validate.py`. It
summarizes the behavior that matters when *producing conforming XML*, distilled
from the W3C XML 1.0 (Fifth Edition) Recommendation — not the full spec.

## Contents
- [Well-formed vs. valid](#well-formed-vs-valid)
- [The DOCTYPE declaration](#the-doctype-declaration)
- [Element type declarations (content models)](#element-type-declarations-content-models)
- [Attribute-list declarations](#attribute-list-declarations)
- [Attribute types](#attribute-types)
- [Attribute defaults](#attribute-defaults)
- [Entities](#entities)
- [Whitespace, encoding, escaping](#whitespace-encoding-escaping)
- [Namespaces and DTDs](#namespaces-and-dtds)
- [Common validity traps](#common-validity-traps)

---

## Well-formed vs. valid
Two distinct bars, and the validator reports them separately:

- **Well-formed** — obeys XML syntax: one root element, every start tag has a
  matching end tag, elements nest properly, attribute values are quoted, only
  defined entities are referenced, reserved characters are escaped. A document
  that is not well-formed is not XML at all; nothing else can be checked until
  it is fixed.
- **Valid** — well-formed *and* conforms to its DTD: the elements, their nesting
  and order, and their attributes all match what the DTD declares.

You can have well-formed-but-invalid XML (e.g. an element in the wrong place).
You cannot have valid-but-not-well-formed XML.

## The DOCTYPE declaration
A document associates itself with a DTD via a document type declaration:

```xml
<!DOCTYPE rootName SYSTEM "grammar.dtd">     <!-- external subset -->
<!DOCTYPE rootName [ ...declarations... ]>   <!-- internal subset -->
<!DOCTYPE rootName SYSTEM "g.dtd" [ ... ]>   <!-- both; internal wins on conflict -->
```

The name right after `<!DOCTYPE` MUST be the name of the root (document)
element. If the actual root element differs, the document is invalid
(`root-mismatch`). The validator accepts the DTD either from the instance's own
DOCTYPE or as an explicit file argument.

## Element type declarations (content models)
`<!ELEMENT name contentmodel>` declares what an element may contain. Five forms:

- **EMPTY** — `<!ELEMENT br EMPTY>`. No content at all: write `<br/>` or
  `<br></br>`. Any child element *or* character data (even whitespace) is a
  validity error.
- **ANY** — `<!ELEMENT note ANY>`. Any mix of character data and declared
  elements, in any order. Children must still themselves be declared.
- **Element content** — a parenthesized expression of child elements with no
  `#PCDATA`. Order and counts are enforced, and **non-whitespace text between
  the children is not allowed** (whitespace is). Operators:
  - sequence `,` — `(title, author, year)` must appear in that order.
  - choice `|` — `(email | phone)` exactly one of them.
  - occurrence: `?` optional (0 or 1), `*` zero or more, `+` one or more,
    none = exactly one. Applies to a name or a parenthesized group.
  - nesting — `(title, (author+, editor?), year?)`.
- **Mixed content** — `(#PCDATA | a | b)*`. Character data freely interspersed
  with any number of `a`/`b` children **in any order**. The only legal forms are
  `(#PCDATA)` (text only) and `(#PCDATA | names...)*` — the `*` and the leading
  `#PCDATA` are mandatory. You cannot constrain the count or order of children in
  mixed content; if you need order, it isn't mixed content.

When the validator says *"children do not match its content model"*, line up the
actual child sequence against the declared expression: usually a missing
required child, an extra one, or wrong order.

## Attribute-list declarations
`<!ATTLIST element  attName attType default  attName2 attType2 default2 ...>`
declares the attributes an element may carry, their types, and their defaults.
An attribute that appears on an element but is **not declared** for that element
is a validity error (`undeclared-attribute`).

## Attribute types
- **CDATA** — any character string. No constraint beyond well-formedness.
- **Enumeration** — `(draft|review|final)`. The value MUST be exactly one of the
  listed tokens (`attr-enum` on failure). This is the DTD analog of a JSON
  Schema `enum`, and the most common place a mapping needs the user's call: map
  source `"M"` → which token? Ask rather than guess.
- **NMTOKEN / NMTOKENS** — value(s) must be name tokens (letters, digits, and
  `. - _ :`; no spaces within a token). NMTOKENS is a whitespace-separated list.
- **ID** — a document-unique identifier; the value must be an XML Name and no two
  ID-typed attributes may share a value (`id-duplicate` on a clash). An element
  may have at most one ID attribute.
- **IDREF / IDREFS** — must match the value of some ID attribute in the same
  document; a reference to a non-existent ID is a validity error
  (`idref-unresolved`). IDREFS is a whitespace-separated list of references.
- **ENTITY / ENTITIES** — must name unparsed entities declared in the DTD.
- **NOTATION (a|b)** — the value names one of the listed notations.

The validator checks enumerations, NMTOKEN(S), ID uniqueness, and IDREF(S)
resolution. CDATA/ENTITY values it cannot fully constrain.

## Attribute defaults
The token after the type controls presence:

- **#REQUIRED** — must be supplied on every instance of the element. Missing it
  is `attr-required`. There is no fallback value.
- **#IMPLIED** — optional, no default. If absent, the attribute simply isn't
  there. (This is the right choice for genuinely optional data.)
- **#FIXED "value"** — must always have exactly this value; supplying a different
  value is `attr-fixed`. If omitted, the processor reports it *as if* set to the
  fixed value.
- **literal default** — `attName CDATA "n/a"`. Optional; if omitted, the
  processor supplies the default. So the attribute is effectively always present
  with at least the default. (Expat applies these automatically, which is why a
  validated document with defaulted attributes still passes.)

Mapping note: a required attribute with no source data is the attribute analog of
a missing required field — surface it instead of inventing a value.

## Entities
- **General entities** — `<!ENTITY company "Acme Corp">`, referenced in content
  as `&company;`. Use them for reusable text. The five **predefined** entities
  need no declaration: `&lt; &gt; &amp; &apos; &quot;`.
- **Character references** — `&#169;` / `&#xA9;` insert a character by code point.
- **Parameter entities** — `<!ENTITY % name "...">`, referenced as `%name;`
  *inside the DTD* to factor out repeated declaration fragments. They affect the
  grammar, not the document content.
- **Unparsed entities + NOTATION** — for binary/non-XML data, referenced via
  ENTITY-typed attributes. Rare in practice.

A reference to an undeclared general entity makes the document **not well-formed**
— expat stops and the validator reports a well-formedness error.

## Whitespace, encoding, escaping
- In element content (not mixed), whitespace between child elements is
  insignificant and allowed; non-whitespace text is not.
- Escape `<` as `&lt;` and `&` as `&amp;` anywhere they would otherwise start
  markup. In attribute values also escape the quote you delimit with.
- Declare the encoding in the XML declaration when it isn't UTF-8:
  `<?xml version="1.0" encoding="ISO-8859-1"?>`.
- A `CDATA` *section* — `<![CDATA[ raw <text> & stuff ]]>` — lets you include
  literal markup characters without escaping (distinct from the CDATA attribute
  type).

## Namespaces and DTDs
DTDs predate XML Namespaces and are not namespace-aware: a DTD validates against
the literal element/attribute names *including* any prefix (`gml:point`, not
`point`). `xmlns`/`xmlns:*` declarations are themselves attributes that a strict
DTD would need to declare. Because of this friction, the validator does **not**
flag `xmlns`/`xmlns:*` as undeclared. If a DTD and namespaces must coexist,
declare the prefixed names exactly as they appear and add the `xmlns:*`
attributes to the relevant `<!ATTLIST>` (often as `#IMPLIED` or `#FIXED`).

## Common validity traps
- A declaration that isn't there imposes no rule — don't invent constraints the
  DTD doesn't state, and don't add elements/attributes it doesn't declare.
- Element content forbids stray text; mixed content allows it. Putting text in an
  element-content element is the most common silent mistake.
- Order matters in a sequence `( , )` but not in mixed content or `ANY`.
- `?`/`*` make a child optional; `+`/none make at least one mandatory. A missing
  mandatory child fails even if everything else is correct.
- Enumerated and `NOTATION` attribute values are exact-match tokens — same
  ambiguity as a JSON `enum`; ask when the source value's mapping isn't obvious.
- IDs must be unique and valid Names; IDREFs must resolve. Reusing an ID or
  pointing at a missing one both fail.
- `#FIXED` means the value is not yours to choose — emit exactly the fixed value
  or omit the attribute entirely.
